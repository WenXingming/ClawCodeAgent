"""负责构造持久化快照与最终运行结果。"""

from __future__ import annotations

from dataclasses import dataclass

from agent.run_state import AgentRunState
from core_contracts.budget import BudgetConfig
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.run_result import AgentRunResult
from core_contracts.runtime_policy import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from openai_client import OpenAIClient
from session import AgentSessionSnapshot, SessionManager


@dataclass
class ResultFactory:
    """集中构造 session 快照与 AgentRunResult。"""

    client: OpenAIClient
    workspace_scope: WorkspaceScope
    execution_policy: ExecutionPolicy
    context_policy: ContextPolicy
    permissions: ToolPermissionPolicy
    budget_config: BudgetConfig
    session_paths: SessionPaths
    session_manager: SessionManager

    def build(self, run_state: AgentRunState) -> AgentRunResult:
        """基于当前运行态落盘并返回标准结果对象。"""
        transcript = run_state.session_state.transcript()
        events_snapshot = tuple(dict(item) for item in run_state.events)
        delta_cost = self.client.model_config.pricing.estimate_cost_usd(run_state.usage_delta)
        total_cost_usd = run_state.cost_baseline + delta_cost
        session_snapshot = AgentSessionSnapshot(
            session_id=run_state.session_id,
            model_config=self.client.model_config,
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
        session_path = self.session_manager.save_session(session_snapshot)
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