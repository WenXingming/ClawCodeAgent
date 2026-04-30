"""负责构造持久化快照与最终运行结果。

ResultFactory 集中封装了从 AgentRunState 构建 AgentSessionSnapshot 并落盘到
 SessionGateway 的全部逻辑，同时输出符合 core_contracts.outcomes.AgentRunResult 的
标准化结果对象。该模块不依赖主循环内部逻辑，仅依赖注入的静态配置和 SessionGateway。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.run_state import AgentRunState
from core_contracts.config import BudgetConfig
from core_contracts.model import ModelClient, ModelConfig
from core_contracts.config import ToolPermissionPolicy
from core_contracts.outcomes import AgentRunResult
from core_contracts.config import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from core_contracts.session import AgentSessionSnapshot
from session.session_gateway import SessionGateway


@dataclass
class ResultFactory:
    """集中构造 session 快照与 AgentRunResult。

    核心工作流：
    1. build() 接收 run_state，计算 usage_total 和 cost 算术；
    2. 将静态配置与运行态内容合并构建 AgentSessionSnapshot；
    3. 通过注入的 SessionGateway 落盘快照；
    4. 构建并返回 AgentRunResult。

    注入的核心依赖：
      - client: 僅用于快照内的模型引用；
      - model_config: 定价计算与快照内配置存储；
      - session_gateway: 落盘快照的存储门面。
    """

    client: ModelClient  # ModelClient: 仅用于快照内存储的模型客户端引用。
    model_config: ModelConfig  # ModelConfig: 当前运行使用的模型配置和定价信息。
    workspace_scope: WorkspaceScope  # WorkspaceScope: 工作区范围，存入快照。
    execution_policy: ExecutionPolicy  # ExecutionPolicy: 执行限制配置，存入快照。
    context_policy: ContextPolicy  # ContextPolicy: 上下文治理配置，存入快照。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy: 工具权限配置，存入快照。
    budget_config: BudgetConfig  # BudgetConfig: 预算配置，存入快照。
    session_paths: SessionPaths  # SessionPaths: 会话路径配置，存入快照。
    session_gateway: SessionGateway  # SessionGateway: 会话快照落盘门面。

    def build(self, run_state: AgentRunState) -> AgentRunResult:
        """基于当前运行态落盘并返回标准结果对象。
        Args:
            run_state (AgentRunState): 包含全部结果信息的当前动态运行态。
        Returns:
            AgentRunResult: 落盘后的标准化运行结果对象。
        Raises:
            ValueError: 当 session_id 非法时由 session_gateway 抛出。
        """
        transcript = run_state.session_state.transcript()
        events_snapshot = tuple(dict(item) for item in run_state.events)
        delta_cost = self.model_config.pricing.estimate_cost_usd(run_state.usage_delta)
        total_cost_usd = run_state.cost_baseline + delta_cost
        session_snapshot = AgentSessionSnapshot(
            session_id=run_state.session_id,
            model_config=self.model_config,
            workspace_scope=self.workspace_scope,
            execution_policy=self.execution_policy,
            context_policy=self.context_policy,
            permissions=self.permissions,
            budget_config=self.budget_config,
            session_paths=self.session_paths,
            messages=tuple(run_state.session_state.to_messages()),
            transcript=transcript,
            events=events_snapshot,
            final_output=run_state.final_output,
            turns=run_state.turns_total,
            tool_calls=run_state.tool_call_count,
            usage=run_state.usage_total,
            total_cost_usd=total_cost_usd,
            stop_reason=run_state.stop_reason,
            mcp_capability_shortlist=run_state.mcp_capability_candidates(),
            materialized_mcp_capability_handles=run_state.materialized_mcp_capabilities(),
        )
        session_path = self.session_gateway.save_session(session_snapshot)
        return AgentRunResult(
            final_output=run_state.final_output,
            turns=run_state.turns_total,
            tool_calls=run_state.tool_call_count,
            transcript=transcript,
            events=events_snapshot,
            usage=run_state.usage_total,
            total_cost_usd=total_cost_usd,
            stop_reason=run_state.stop_reason,
            file_history=session_snapshot.file_history,
            session_id=run_state.session_id,
            session_path=str(session_path),
            scratchpad_directory=session_snapshot.scratchpad_directory,
        )