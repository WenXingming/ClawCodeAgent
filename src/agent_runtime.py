"""ISSUE-006 LocalCodingAgent 最小闭环实现。

本模块实现最小 run 主循环：
1) 调模型。
2) 执行工具并回填。
3) 达到停止条件后返回 AgentRunResult。

设计原则：简单优先，不引入 resume、压缩、预算闸门等后续能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .agent_tools import AgentTool, build_tool_context, default_tool_registry, execute_tool
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
        """执行一轮端到端任务。"""
        session = AgentSessionState.create(prompt)
        session_id = uuid4().hex
        events: list[JSONDict] = []
        usage_total = TokenUsage()
        final_output = ''
        turns_executed = 0
        stop_reason = 'max_turns'

        tool_context = build_tool_context(self.runtime_config, tool_registry=self.tool_registry)

        for turn_index in range(1, self.runtime_config.max_turns + 1):
            turns_executed = turn_index
            try:
                response = self.client.complete(
                    messages=session.to_messages(),
                    tools=self._build_openai_tools(),
                    output_schema=self.runtime_config.output_schema,
                )
            except OpenAIClientError as exc:
                stop_reason = 'backend_error'
                events.append(
                    {
                        'type': 'backend_error',
                        'turn': turn_index,
                        'error': str(exc),
                    }
                )
                return self._build_run_result(
                    session_id=session_id,
                    session=session,
                    final_output=final_output,
                    turns_executed=turns_executed,
                    usage_total=usage_total,
                    stop_reason=stop_reason,
                    events=events,
                )

            usage_total = usage_total + response.usage
            session.append_assistant_turn(response)
            if response.content:
                final_output = response.content

            events.append(
                {
                    'type': 'model_turn',
                    'turn': turn_index,
                    'finish_reason': response.finish_reason,
                    'tool_calls': len(response.tool_calls),
                }
            )

            # 没有工具调用时，说明当前任务已收敛
            if not response.tool_calls:
                stop_reason = response.finish_reason or 'completed'
                return self._build_run_result(
                    session_id=session_id,
                    session=session,
                    final_output=final_output,
                    turns_executed=turns_executed,
                    usage_total=usage_total,
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
                events.append(
                    {
                        'type': 'tool_result',
                        'turn': turn_index,
                        'tool_call_id': tool_call.id,
                        'tool_name': tool_call.name,
                        'ok': tool_result.ok,
                        'error_kind': tool_result.metadata.get('error_kind'),
                    }
                )

        # 达到最大轮数限制，返回结果
        return self._build_run_result(
            session_id=session_id,
            session=session,
            final_output=final_output,
            turns_executed=turns_executed,
            usage_total=usage_total,
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
        turns_executed: int,
        usage_total: TokenUsage,
        stop_reason: str,
        events: list[JSONDict],
    ) -> AgentRunResult:
        """统一构造最终运行结果并落盘会话快照。"""
        transcript = session.transcript()
        events_snapshot = tuple(dict(item) for item in events)
        total_cost_usd = self.client.config.pricing.estimate_cost_usd(usage_total)
        stored_session = StoredAgentSession(
            session_id=session_id,
            model_config=self.client.config,
            runtime_config=self.runtime_config,
            messages=tuple(session.to_messages()),
            transcript=transcript,
            events=events_snapshot,
            final_output=final_output,
            turns=turns_executed,
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
            turns=turns_executed,
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
