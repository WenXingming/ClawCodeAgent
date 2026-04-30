"""负责 prompt 预处理与 slash 本地结果构造。

PromptProcessor 封装了主循环在 prompt 写入会话前的全部局部决策逻辑：
  - 将 slash 命令分流到 SlashDispatcher 本地处理（不进入模型循环）；
  - 将普通 prompt 写入 session_state；
  - 在 slash 命令被处理后构造并落盘结果。
该模块不进行模型调用，仅负责 prompt 的路由和会话写入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from agent.result_factory import ResultFactory
from agent.run_state import AgentRunState
from core_contracts.config import BudgetConfig
from core_contracts.config import ContextPolicy, WorkspaceScope
from core_contracts.config import ToolPermissionPolicy
from core_contracts.interaction import SlashCommandContext, SlashCommandResult, SlashDispatcher
from core_contracts.model import ModelConfig
from core_contracts.primitives import JSONDict
from core_contracts.outcomes import AgentRunResult
from core_contracts.tools import ToolDescriptor
from workspace import WorkspaceGateway


@dataclass
class PromptProcessor:
    """处理 slash 分流与 prompt 写入前的本地决策。

    注入的核心依赖：
      - slash_dispatcher: 实现 SlashDispatcher 协议，解析并分发 slash 命令；
      - workspace_gateway: 提供插件摘要，用于 /tools /permissions 等命令的上下文；
      - tool_registry_getter: 延迟取得当前有效工具表的回调；
      - result_factory: 将 slash 处理结果落盘为 AgentRunResult。
    """

    slash_dispatcher: SlashDispatcher  # SlashDispatcher: slash 命令解析和分发协议实现。
    workspace_scope: WorkspaceScope  # WorkspaceScope: 工作区范围，传入 slash 命令上下文。
    context_policy: ContextPolicy  # ContextPolicy: 上下文治理配置，传入 slash 命令上下文。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy: 工具权限配置，传入 slash 命令上下文。
    budget_config: BudgetConfig  # BudgetConfig: 预算配置，传入 slash 命令上下文。
    model_config: ModelConfig  # ModelConfig: 模型配置，传入 slash 命令上下文。
    workspace_gateway: WorkspaceGateway  # WorkspaceGateway: 工作区插件摘要提供者。
    tool_registry_getter: Callable[[], dict[str, ToolDescriptor]]  # Callable: 延迟取得有效工具表的回调。
    result_factory: ResultFactory  # ResultFactory: slash 结果落盘器。

    def prepare(
        self,
        *,
        prompt: str,
        run_state: AgentRunState,
    ) -> AgentRunResult | None:
        """在 prompt 写入 session_state 前执行 slash 分流。
        Args:
            prompt (str): 当前用户输入。
            run_state (AgentRunState): 当前调用共享的动态运行态对象。
        Returns:
            AgentRunResult | None: slash 命令被本地处理时返回结果；
                continue_query 或普通 prompt 时返回 None。
        Raises:
            无。
        """
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
        """构造本地 slash 命令结果并落盘。
        Args:
            slash_result (SlashCommandResult): slash 分发器返回的局部处理结果。
            run_state (AgentRunState): 当前调用共享的动态运行态对象。
        Returns:
            AgentRunResult: 经过 result_factory 落盘后的最终运行结果。
        Raises:
            无。
        """
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
        """为本地 slash 命令构造统一事件载荷。
        Args:
            slash_result (SlashCommandResult): slash 分发器返回的处理结果。
            session_id_before (str): 执行 slash 命令前的 session ID。
            session_id_after (str): 执行 slash 命令后的 session ID（fork 时不同）。
        Returns:
            JSONDict: 适合写入事件列表的结构化事件字典。
        Raises:
            无。
        """
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
        """把 slash 处理结果格式化为最终输出文本。
        Args:
            slash_result (SlashCommandResult): slash 分发器返回的处理结果。
            session_id_before (str): 执行 slash 前的 session ID。
            session_id_after (str): 执行 slash 后的 session ID。
        Returns:
            str: 面向用户和模型的可读输出文本。
        Raises:
            无。
        """
        if slash_result.command_name != 'clear':
            return slash_result.output

        lines = [slash_result.output]
        if slash_result.metadata.get('had_history'):
            lines.append(f'Previous session id: {session_id_before}')
        lines.append(f'Cleared session id: {session_id_after}')
        return '\n'.join(lines)