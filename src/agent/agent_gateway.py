"""Minimal Agent gateway skeleton.

This implementation intentionally keeps only the framework surface and does not
wire any runtime modules. It is used to isolate other domain refactors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from core_contracts.config import BudgetConfig
from core_contracts.config import ContextPolicy, ExecutionPolicy, SessionPaths, ToolPermissionPolicy, WorkspaceScope
from core_contracts.model import ModelClient, ModelConfig
from core_contracts.outcomes import AgentRunResult
from core_contracts.primitives import TokenUsage
from core_contracts.session_contracts import AgentSessionSnapshot


@dataclass
class AgentGateway:
    """agent 领域最小公开骨架。

    该类仅保留稳定对外接口，不接入任何内部运行模块，便于隔离重构。
    """

    client: ModelClient  # ModelClient：模型客户端占位依赖。
    workspace_scope: WorkspaceScope  # WorkspaceScope：工作区范围配置。
    execution_policy: ExecutionPolicy  # ExecutionPolicy：执行约束配置。
    context_policy: ContextPolicy  # ContextPolicy：上下文策略配置。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy：权限配置。
    session_paths: SessionPaths  # SessionPaths：会话路径配置。
    session_gateway: Any  # Any：会话网关占位依赖。
    budget_config: BudgetConfig | None = None  # BudgetConfig | None：预算配置。
    model_config: ModelConfig | None = None  # ModelConfig | None：模型配置。
    tool_gateway: Any | None = None  # Any | None：工具网关占位依赖。
    delegation_service: Any | None = None  # Any | None：委派服务占位依赖。
    current_agent_id: str | None = None  # str | None：当前代理标识。

    def run(self, prompt: str) -> AgentRunResult:
        """执行骨架模式下的一次新会话运行。
        Args:
            prompt (str): 本轮用户输入。
        Returns:
            AgentRunResult: 骨架运行结果。
        Raises:
            无。
        """
        session_id = uuid4().hex
        return self._run_loop(prompt=prompt, session_id=session_id, resumed_from_session_id=None)

    def resume(self, prompt: str, session_snapshot: AgentSessionSnapshot) -> AgentRunResult:
        """执行骨架模式下的一次恢复会话运行。
        Args:
            prompt (str): 本轮用户输入。
            session_snapshot (AgentSessionSnapshot): 待恢复会话快照。
        Returns:
            AgentRunResult: 骨架运行结果。
        Raises:
            无。
        """
        return self._run_loop(
            prompt=prompt,
            session_id=session_snapshot.session_id,
            resumed_from_session_id=session_snapshot.session_id,
        )

    def _run_loop(
        self,
        *,
        prompt: str,
        session_id: str,
        resumed_from_session_id: str | None,
    ) -> AgentRunResult:
        """运行最小骨架循环并返回结果。
        Args:
            prompt (str): 用户输入文本。
            session_id (str): 当前运行使用的会话 ID。
            resumed_from_session_id (str | None): 恢复来源会话 ID。
        Returns:
            AgentRunResult: 统一骨架结果对象。
        Raises:
            无。
        """
        while False:  # TODO: 实现循环条件。
            # TODO: 实现骨架运行循环。
            pass
        return AgentRunResult()  # TODO: 实现骨架运行循环。


