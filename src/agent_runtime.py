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

from .agent_tools import AgentTool, build_tool_context, default_tool_registry, execute_tool
from .context import BudgetGuard, SnipResult, check_token_budget, snip_session
from .contract_types import (
    AgentRunResult,
    AgentRuntimeConfig,
    JSONDict,
    TokenUsage,
)
from .openai_client import OpenAIClient, OpenAIClientError
from .session import AgentSessionState, StoredAgentSession, save_agent_session


@dataclass
class LocalCodingAgent:
    """最小可用的本地编码代理。"""

    client: OpenAIClient  # 模型客户端。
    runtime_config: AgentRuntimeConfig  # 运行配置。
    tool_registry: dict[str, AgentTool] = field(default_factory=default_tool_registry)  # 可用工具集合。

    def run(self, prompt: str) -> AgentRunResult:
        """执行一轮端到端任务（新会话）。"""
        return self._execute_loop(
            session=AgentSessionState.create(prompt),
            session_id=uuid4().hex,
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
        session.append_user(prompt)
        return self._execute_loop(
            session=session,
            session_id=stored_session.session_id,
            turns_offset=stored_session.turns,
            usage_baseline=stored_session.usage,
            cost_baseline=stored_session.total_cost_usd,
        )

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

            # token_budget event 始终记录（snip 后状态，供观测）
            events.append({
                'type': 'token_budget',
                'turn': turn_index,
                'projected': snapshot.projected_input_tokens,
                'is_hard_over': snapshot.is_hard_over,
                'is_soft_over': snapshot.is_soft_over,
            })

            # 模型调用前四维预算检查（session_turns / model_calls / token / cost）
            if stop := guard.check_pre_model(
                turns_offset=turns_offset,
                turns_this_run=turns_this_run,
                model_call_count=model_call_count,
                snapshot=snapshot,
                usage_delta=usage_delta,
            ):
                return self._early_stop(
                    stop,
                    session_id=session_id, session=session, final_output=final_output,
                    turns_total=turns_offset + turns_this_run, usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta, cost_baseline=cost_baseline,
                    turn_index=turn_index, events=events,
                )

            try:
                response = self.client.complete(
                    messages=session.to_messages(),
                    tools=openai_tools,
                    output_schema=self.runtime_config.output_schema,
                )
            except OpenAIClientError as exc:
                stop_reason = 'backend_error'
                events.append({'type': 'backend_error', 'turn': turn_index, 'error': str(exc)})
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

            model_call_count += 1
            usage_delta = usage_delta + response.usage
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
