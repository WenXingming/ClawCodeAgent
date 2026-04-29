"""负责 prompt 预处理与 slash 本地结果构造。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from agent.result_factory import ResultFactory
from agent.run_state import AgentRunState
from core_contracts.budget import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict
from core_contracts.run_result import AgentRunResult
from core_contracts.runtime_policy import ContextPolicy, WorkspaceScope
from interaction.slash_commands import SlashCommandContext, SlashCommandDispatcher, SlashCommandResult
from tools.registry import LocalTool
from workspace import WorkspaceGateway


@dataclass
class PromptProcessor:
    """处理 slash 分流与 prompt 写入前的本地决策。"""

    slash_dispatcher: SlashCommandDispatcher
    workspace_scope: WorkspaceScope
    context_policy: ContextPolicy
    permissions: ToolPermissionPolicy
    budget_config: BudgetConfig
    model_config: ModelConfig
    workspace_gateway: WorkspaceGateway
    tool_registry_getter: Callable[[], dict[str, LocalTool]]
    result_factory: ResultFactory

    def prepare(
        self,
        *,
        prompt: str,
        run_state: AgentRunState,
    ) -> AgentRunResult | None:
        """在 prompt 写入 session_state 前执行 slash 分流。"""
        slash_result = self.slash_dispatcher.dispatch_slash_command(
            SlashCommandContext(
                session_state=run_state.session_state,
                session_id=run_state.session_id,
                turns_offset=run_state.turns_offset,
                tool_call_count=run_state.tool_call_count,
                workspace_scope=self.workspace_scope,
                context_policy=self.context_policy,
                permissions=self.permissions,
                budget_config=self.budget_config,
                model_config=self.model_config,
                tool_registry=self.tool_registry_getter(),
                plugin_summary=self.workspace_gateway.render_plugin_summary(),
            ),
            prompt,
        )

        if not slash_result.handled:
            run_state.session_state.append_user(slash_result.prompt or prompt)
            return None

        if slash_result.continue_query:
            run_state.session_state.append_user(slash_result.prompt or prompt)
            return None

        return self._build_slash_result(slash_result, run_state=run_state)

    def _build_slash_result(
        self,
        slash_result: SlashCommandResult,
        *,
        run_state: AgentRunState,
    ) -> AgentRunResult:
        """构造本地 slash 命令结果并落盘。"""
        effective_session_state = slash_result.replacement_session_state or run_state.session_state
        effective_session_id = uuid4().hex if slash_result.fork_session else run_state.session_id

        if slash_result.fork_session:
            effective_run_state = AgentRunState.for_new_session(
                session_state=effective_session_state,
                session_id=effective_session_id,
            )
        else:
            effective_run_state = AgentRunState.for_resumed_session(
                session_state=effective_session_state,
                session_id=effective_session_id,
                turns_offset=run_state.turns_offset,
                usage_baseline=run_state.usage_baseline,
                cost_baseline=run_state.cost_baseline,
                tool_call_count=run_state.tool_call_count,
                mcp_capability_shortlist=run_state.mcp_capability_candidates(),
                materialized_mcp_capability_handles=run_state.materialized_mcp_capabilities(),
            )

        effective_run_state.final_output = self._format_slash_output(
            slash_result,
            session_id_before=run_state.session_id,
            session_id_after=effective_session_id,
        )
        effective_run_state.stop_reason = 'slash_command'
        effective_run_state.events.append(
            self._make_slash_event(
                slash_result,
                session_id_before=run_state.session_id,
                session_id_after=effective_session_id,
            )
        )
        return self.result_factory.build(effective_run_state)

    @staticmethod
    def _make_slash_event(
        slash_result: SlashCommandResult,
        *,
        session_id_before: str,
        session_id_after: str,
    ) -> JSONDict:
        """为本地 slash 命令构造统一事件载荷。"""
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
        """把 slash 处理结果格式化为最终输出文本。"""
        if slash_result.command_name != 'clear':
            return slash_result.output

        lines = [slash_result.output]
        if slash_result.metadata.get('had_history'):
            lines.append(f'Previous session id: {session_id_before}')
        lines.append(f'Cleared session id: {session_id_after}')
        return '\n'.join(lines)