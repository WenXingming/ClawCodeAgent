"""ISSUE-006 LocalCodingAgent 最小闭环实现。

本模块实现最小 run 主循环：
1) 调模型。
2) 执行工具并回填。
3) 达到停止条件后返回 AgentRunResult。

ISSUE-008 扩展：
4) resume(prompt, stored_session) 从持久化会话恢复并继续执行。
   - 严格继承 stored_session 的 model/runtime 配置。
   - usage/cost/turns/tool_calls 从历史基线累计。
   - session_id 保持不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from control_plane.slash_commands import SlashCommandContext, SlashCommandResult, dispatch_slash_command
from context.budget_guard import BudgetGuard
from context.compact import (
    CompactResult,
    compact_conversation,
    is_context_length_error,
    should_auto_compact,
)
from context.snip import snip_session
from context.token_budget import check_token_budget
from core_contracts.config import AgentRuntimeConfig
from core_contracts.protocol import JSONDict, OneTurnResponse
from core_contracts.result import AgentRunResult
from core_contracts.usage import TokenUsage
from openai_client.openai_client import OpenAIClient, OpenAIClientError
from session.session_contracts import StoredAgentSession
from session.session_state import AgentSessionState
from session.session_store import save_agent_session
from tools.agent_tools import AgentTool, build_tool_context, default_tool_registry, execute_tool


_MAX_REACTIVE_COMPACT_RETRIES = 2


@dataclass
class LocalCodingAgent:
    """最小可用的本地编码代理。"""

    client: OpenAIClient  # 模型客户端。
    runtime_config: AgentRuntimeConfig  # 运行配置。
    tool_registry: dict[str, AgentTool] = field(default_factory=default_tool_registry)  # 可用工具集合。

    def run(self, prompt: str) -> AgentRunResult:
        """执行一轮端到端任务（新会话）。"""
        session = AgentSessionState()
        session_id = uuid4().hex
        local_result = self._prepare_prompt(
            prompt=prompt,
            session=session,
            session_id=session_id,
            turns_offset=0,
            usage_baseline=TokenUsage(),
            cost_baseline=0.0,
        )
        if local_result is not None:
            return local_result
        return self._execute_loop(
            session=session,
            session_id=session_id,
            turns_offset=0,
            usage_baseline=TokenUsage(),
            cost_baseline=0.0,
        )

    def resume(self, prompt: str, stored_session: StoredAgentSession) -> AgentRunResult:
        """从已保存的会话恢复并继续执行新 prompt。

        严格继承 stored_session 的 model/runtime 配置；
        usage、turns、tool_calls 从历史基线累计；
        cost = 历史成本 + 本次 delta 成本；
        session_id 保持不变。
        """
        session = AgentSessionState.from_persisted(
            messages=list(stored_session.messages),
            transcript=list(stored_session.transcript),
            tool_call_count=stored_session.tool_calls,
        )
        local_result = self._prepare_prompt(
            prompt=prompt,
            session=session,
            session_id=stored_session.session_id,
            turns_offset=stored_session.turns,
            usage_baseline=stored_session.usage,
            cost_baseline=stored_session.total_cost_usd,
        )
        if local_result is not None:
            return local_result
        return self._execute_loop(
            session=session,
            session_id=stored_session.session_id,
            turns_offset=stored_session.turns,
            usage_baseline=stored_session.usage,
            cost_baseline=stored_session.total_cost_usd,
        )

    def _prepare_prompt(
        self,
        *,
        prompt: str,
        session: AgentSessionState,
        session_id: str,
        turns_offset: int,
        usage_baseline: TokenUsage,
        cost_baseline: float,
    ) -> AgentRunResult | None:
        """在 prompt 写入 session 前执行 slash 分流。"""
        slash_result = dispatch_slash_command(
            SlashCommandContext(
                session=session,
                session_id=session_id,
                turns_offset=turns_offset,
                runtime_config=self.runtime_config,
                model_config=self.client.config,
                tool_registry=self.tool_registry,
            ),
            prompt,
        )

        if not slash_result.handled:
            session.append_user(slash_result.prompt or prompt)
            return None

        if slash_result.continue_query:
            session.append_user(slash_result.prompt or prompt)
            return None

        return self._build_slash_result(
            slash_result,
            session=session,
            session_id=session_id,
            turns_offset=turns_offset,
            usage_baseline=usage_baseline,
            cost_baseline=cost_baseline,
        )

    def _build_slash_result(
        self,
        slash_result: SlashCommandResult,
        *,
        session: AgentSessionState,
        session_id: str,
        turns_offset: int,
        usage_baseline: TokenUsage,
        cost_baseline: float,
    ) -> AgentRunResult:
        """构造本地 slash 命令结果并落盘。"""
        effective_session = slash_result.replacement_session or session
        effective_session_id = uuid4().hex if slash_result.fork_session else session_id

        if slash_result.fork_session:
            effective_turns = 0
            effective_usage_total = TokenUsage()
            effective_usage_delta = TokenUsage()
            effective_cost_baseline = 0.0
        else:
            effective_turns = turns_offset
            effective_usage_total = usage_baseline
            effective_usage_delta = TokenUsage()
            effective_cost_baseline = cost_baseline

        event = self._make_slash_event(
            slash_result,
            session_id_before=session_id,
            session_id_after=effective_session_id,
        )
        return self._build_run_result(
            session_id=effective_session_id,
            session=effective_session,
            final_output=self._format_slash_output(
                slash_result,
                session_id_before=session_id,
                session_id_after=effective_session_id,
            ),
            turns_total=effective_turns,
            usage_delta=effective_usage_delta,
            usage_total=effective_usage_total,
            cost_baseline=effective_cost_baseline,
            stop_reason='slash_command',
            events=[event],
        )

    @staticmethod
    def _make_slash_event(
        slash_result: SlashCommandResult,
        *,
        session_id_before: str,
        session_id_after: str,
    ) -> JSONDict:
        event: JSONDict = {
            'type': 'slash_command',
            'command': slash_result.command_name,
            'continue_query': slash_result.continue_query,
            'mode': 'mutating' if slash_result.fork_session else 'read_only',
            'session_id_before': session_id_before,
            'session_id_after': session_id_after,
        }
        for key, value in slash_result.metadata.items():
            event[key] = value
        return event

    @staticmethod
    def _format_slash_output(
        slash_result: SlashCommandResult,
        *,
        session_id_before: str,
        session_id_after: str,
    ) -> str:
        if slash_result.command_name != 'clear':
            return slash_result.output

        lines = [slash_result.output]
        if slash_result.metadata.get('had_history'):
            lines.append(f'Previous session id: {session_id_before}')
        lines.append(f'Cleared session id: {session_id_after}')
        return '\n'.join(lines)

    def _execute_loop(
        self,
        *,
        session: AgentSessionState,
        session_id: str,
        turns_offset: int,
        usage_baseline: TokenUsage,
        cost_baseline: float,
    ) -> AgentRunResult:
        """run / resume 共用的 turn loop。

        usage_delta  只统计本次执行的增量。
        usage_total  = usage_baseline + usage_delta。
        cost         = cost_baseline + estimate_cost_usd(usage_delta)。
        """
        events: list[JSONDict] = []
        usage_delta = TokenUsage()
        final_output = ''
        turns_this_run = 0
        stop_reason = 'max_turns'
        model_call_count = 0

        guard = BudgetGuard(
            budget=self.runtime_config.budget_config,
            pricing=self.client.config.pricing,
            cost_baseline=cost_baseline,
        )
        tool_context = build_tool_context(self.runtime_config, tool_registry=self.tool_registry)

        for turn_index in range(1, self.runtime_config.max_turns + 1):
            turns_this_run = turn_index

            # token preflight
            openai_tools = self._build_openai_tools()
            snapshot = check_token_budget(
                messages=session.to_messages(),
                tools=openai_tools,
                max_input_tokens=self.runtime_config.budget_config.max_input_tokens,
            )

            # ISSUE-010 snip：soft_over 时就地剪裁旧消息，降低 prompt 压力
            if snapshot.is_soft_over:
                snip_result = snip_session(
                    session.messages,
                    preserve_messages=self.runtime_config.compact_preserve_messages,
                    tools=openai_tools,
                    max_input_tokens=self.runtime_config.budget_config.max_input_tokens,
                )
                if snip_result.snipped_count > 0:
                    events.append({
                        'type': 'snip_boundary',
                        'turn': turn_index,
                        'snipped_count': snip_result.snipped_count,
                        'tokens_removed': snip_result.tokens_removed,
                    })
                    # 重新计算，token_budget event 反映 snip 后的状态
                    snapshot = check_token_budget(
                        messages=session.to_messages(),
                        tools=openai_tools,
                        max_input_tokens=self.runtime_config.budget_config.max_input_tokens,
                    )

            pre_model_stop = guard.check_pre_model(
                turns_offset=turns_offset,
                turns_this_run=turns_this_run,
                model_call_count=model_call_count,
                snapshot=snapshot,
                usage_delta=usage_delta,
            )

            if (
                should_auto_compact(
                    snapshot.projected_input_tokens,
                    self.runtime_config.auto_compact_threshold_tokens,
                )
                and pre_model_stop is None
            ):
                compact_result = compact_conversation(
                    self.client,
                    session.messages,
                    preserve_messages=self.runtime_config.compact_preserve_messages,
                )
                if compact_result.compacted:
                    model_call_count += 1
                    usage_delta = usage_delta + compact_result.usage
                    events.append(self._make_compact_event(turn_index, 'auto', compact_result))
                    snapshot = check_token_budget(
                        messages=session.to_messages(),
                        tools=openai_tools,
                        max_input_tokens=self.runtime_config.budget_config.max_input_tokens,
                    )
                    pre_model_stop = guard.check_pre_model(
                        turns_offset=turns_offset,
                        turns_this_run=turns_this_run,
                        model_call_count=model_call_count,
                        snapshot=snapshot,
                        usage_delta=usage_delta,
                    )
                elif compact_result.error:
                    events.append({
                        'type': 'compact_failed',
                        'turn': turn_index,
                        'trigger': 'auto',
                        'error': compact_result.error,
                        'preserve_messages': self.runtime_config.compact_preserve_messages,
                    })

            # token_budget event 始终记录（snip 后状态，供观测）
            events.append({
                'type': 'token_budget',
                'turn': turn_index,
                'projected': snapshot.projected_input_tokens,
                'is_hard_over': snapshot.is_hard_over,
                'is_soft_over': snapshot.is_soft_over,
            })

            # 模型调用前四维预算检查（session_turns / model_calls / token / cost）
            if pre_model_stop is not None:
                return self._early_stop(
                    pre_model_stop,
                    session_id=session_id, session=session, final_output=final_output,
                    turns_total=turns_offset + turns_this_run, usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta, cost_baseline=cost_baseline,
                    turn_index=turn_index, events=events,
                )

            response = self._complete_with_reactive_compact(
                session=session,
                openai_tools=openai_tools,
                turn_index=turn_index,
                events=events,
                guard=guard,
                turns_offset=turns_offset,
                turns_this_run=turns_this_run,
                usage_delta=usage_delta,
                model_call_count=model_call_count,
            )
            response, usage_delta, model_call_count, reactive_stop = response
            if reactive_stop is not None:
                return self._early_stop(
                    reactive_stop,
                    session_id=session_id, session=session, final_output=final_output,
                    turns_total=turns_offset + turns_this_run, usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta, cost_baseline=cost_baseline,
                    turn_index=turn_index, events=events,
                )
            if response is None:
                stop_reason = 'backend_error'
                return self._build_run_result(
                    session_id=session_id,
                    session=session,
                    final_output=final_output,
                    turns_total=turns_offset + turns_this_run,
                    usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta,
                    cost_baseline=cost_baseline,
                    stop_reason=stop_reason,
                    events=events,
                )

            session.append_assistant_turn(response)
            if response.content:
                final_output = response.content

            events.append({
                'type': 'model_turn',
                'turn': turn_index,
                'finish_reason': response.finish_reason,
                'tool_calls': len(response.tool_calls),
            })

            # 没有工具调用时，说明当前任务已收敛
            if not response.tool_calls:
                stop_reason = response.finish_reason or 'completed'
                return self._build_run_result(
                    session_id=session_id,
                    session=session,
                    final_output=final_output,
                    turns_total=turns_offset + turns_this_run,
                    usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta,
                    cost_baseline=cost_baseline,
                    stop_reason=stop_reason,
                    events=events,
                )

            # 执行工具调用并回填结果
            for tool_call in response.tool_calls:
                tool_result = execute_tool(
                    self.tool_registry,
                    tool_call.name,
                    tool_call.arguments,
                    tool_context,
                )
                session.append_tool_result(tool_call, tool_result)
                events.append({
                    'type': 'tool_result',
                    'turn': turn_index,
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'ok': tool_result.ok,
                    'error_kind': tool_result.metadata.get('error_kind'),
                })

                # 工具执行后预算检查
                if stop := guard.check_post_tool(session.tool_call_count):
                    return self._early_stop(
                        stop,
                        session_id=session_id, session=session, final_output=final_output,
                        turns_total=turns_offset + turns_this_run, usage_delta=usage_delta,
                        usage_total=usage_baseline + usage_delta, cost_baseline=cost_baseline,
                        turn_index=turn_index, events=events,
                    )

        # 达到最大轮数限制，返回结果
        return self._build_run_result(
            session_id=session_id,
            session=session,
            final_output=final_output,
            turns_total=turns_offset + turns_this_run,
            usage_delta=usage_delta,
            usage_total=usage_baseline + usage_delta,
            cost_baseline=cost_baseline,
            stop_reason=stop_reason,
            events=events,
        )

    def _early_stop(
        self,
        stop_reason: str,
        *,
        session_id: str,
        session: AgentSessionState,
        final_output: str,
        turns_total: int,
        usage_delta: TokenUsage,
        usage_total: TokenUsage,
        cost_baseline: float,
        turn_index: int,
        events: list[JSONDict],
    ) -> AgentRunResult:
        """预算闸门触发时的统一提前退出路径。

        统一追加 budget_stop 事件并调用 _build_run_result，
        消除六处重复的事件追加 + 结果构建模式。
        """
        events.append({'type': 'budget_stop', 'reason': stop_reason, 'turn': turn_index})
        return self._build_run_result(
            session_id=session_id,
            session=session,
            final_output=final_output,
            turns_total=turns_total,
            usage_delta=usage_delta,
            usage_total=usage_total,
            cost_baseline=cost_baseline,
            stop_reason=stop_reason,
            events=events,
        )

    def _complete_with_reactive_compact(
        self,
        *,
        session: AgentSessionState,
        openai_tools: list[JSONDict],
        turn_index: int,
        events: list[JSONDict],
        guard: BudgetGuard,
        turns_offset: int,
        turns_this_run: int,
        usage_delta: TokenUsage,
        model_call_count: int,
    ) -> tuple[OneTurnResponse | None, TokenUsage, int, str | None]:
        """执行一次模型调用；必要时在 context-length 错误后进行 reactive compact 重试。"""
        current_usage = usage_delta
        current_model_call_count = model_call_count
        attempt = 0
        current_error: OpenAIClientError | None = None

        while True:
            try:
                response = self.client.complete(
                    messages=session.to_messages(),
                    tools=openai_tools,
                    output_schema=self.runtime_config.output_schema,
                )
                current_model_call_count += 1
                current_usage = current_usage + response.usage
                return response, current_usage, current_model_call_count, None
            except OpenAIClientError as exc:
                current_error = exc
                if not is_context_length_error(exc) or attempt >= _MAX_REACTIVE_COMPACT_RETRIES:
                    break

                attempt += 1
                preserve_messages = max(
                    1,
                    self.runtime_config.compact_preserve_messages - (attempt - 1),
                )
                compact_result = compact_conversation(
                    self.client,
                    session.messages,
                    preserve_messages=preserve_messages,
                )

                retry_event: JSONDict = {
                    'type': 'reactive_compact_retry',
                    'turn': turn_index,
                    'attempt': attempt,
                    'preserve_messages': preserve_messages,
                    'context_error': str(exc),
                }

                if not compact_result.compacted:
                    retry_event['ok'] = False
                    retry_event['error'] = compact_result.error or 'Reactive compact made no progress'
                    events.append(retry_event)
                    break

                current_model_call_count += 1
                current_usage = current_usage + compact_result.usage
                retry_event['ok'] = True
                retry_event['tokens_removed'] = compact_result.tokens_removed
                retry_event['messages_replaced'] = compact_result.messages_replaced
                events.append(self._make_compact_event(turn_index, 'reactive', compact_result, attempt=attempt))
                events.append(retry_event)

                snapshot = check_token_budget(
                    messages=session.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=self.runtime_config.budget_config.max_input_tokens,
                )
                if stop := guard.check_pre_model(
                    turns_offset=turns_offset,
                    turns_this_run=turns_this_run,
                    model_call_count=current_model_call_count,
                    snapshot=snapshot,
                    usage_delta=current_usage,
                ):
                    return None, current_usage, current_model_call_count, stop

        events.append({'type': 'backend_error', 'turn': turn_index, 'error': str(current_error)})
        return None, current_usage, current_model_call_count, None

    @staticmethod
    def _make_compact_event(
        turn_index: int,
        trigger: str,
        result: CompactResult,
        *,
        attempt: int | None = None,
    ) -> JSONDict:
        event: JSONDict = {
            'type': 'compact_boundary',
            'turn': turn_index,
            'trigger': trigger,
            'messages_replaced': result.messages_replaced,
            'tokens_removed': result.tokens_removed,
            'pre_tokens': result.pre_tokens,
            'post_tokens': result.post_tokens,
            'preserve_messages': result.preserve_messages_used,
        }
        if attempt is not None:
            event['attempt'] = attempt
        return event

    def _build_openai_tools(self) -> list[JSONDict]:
        """构建发送给模型的工具定义列表。"""
        return [tool.to_openai_tool() for tool in self.tool_registry.values()]

    def _build_run_result(
        self,
        *,
        session_id: str,
        session: AgentSessionState,
        final_output: str,
        turns_total: int,
        usage_delta: TokenUsage,
        usage_total: TokenUsage,
        cost_baseline: float,
        stop_reason: str,
        events: list[JSONDict],
    ) -> AgentRunResult:
        """统一构造最终运行结果并落盘会话快照。

        total_cost_usd = cost_baseline + estimate_cost_usd(usage_delta)，
        避免因历史计费策略变化导致重算偏差。
        """
        transcript = session.transcript()
        events_snapshot = tuple(dict(item) for item in events)
        delta_cost = self.client.config.pricing.estimate_cost_usd(usage_delta)
        total_cost_usd = cost_baseline + delta_cost
        stored_session = StoredAgentSession(
            session_id=session_id,
            model_config=self.client.config,
            runtime_config=self.runtime_config,
            messages=tuple(session.to_messages()),
            transcript=transcript,
            events=events_snapshot,
            final_output=final_output,
            turns=turns_total,
            tool_calls=session.tool_call_count,
            usage=usage_total,
            total_cost_usd=total_cost_usd,
            stop_reason=stop_reason,
        )
        session_path = save_agent_session(
            stored_session,
            directory=self.runtime_config.session_directory,
        )
        return AgentRunResult(
            final_output=final_output,
            turns=turns_total,
            tool_calls=session.tool_call_count,
            transcript=transcript,
            events=events_snapshot,
            usage=usage_total,
            total_cost_usd=total_cost_usd,
            stop_reason=stop_reason,
            file_history=stored_session.file_history,
            session_id=session_id,
            session_path=str(session_path),
            scratchpad_directory=stored_session.scratchpad_directory,
        )
