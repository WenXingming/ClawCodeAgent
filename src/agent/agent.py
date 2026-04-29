"""agent 领域统一对外入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from agent.delegation_service import DelegationService
from agent.prompt_processor import PromptProcessor
from agent.result_factory import ResultFactory
from agent.run_state import AgentRunState
from agent.turn_coordinator import TurnCoordinator
from context import ContextManager
from core_contracts.budget import BudgetConfig
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict
from core_contracts.run_result import AgentRunResult
from core_contracts.runtime_policy import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from interaction.slash_commands import SlashCommandDispatcher
from openai_client.openai_client import OpenAIClient
from session.session_snapshot import AgentSessionSnapshot
from session.session_state import AgentSessionState
from session.session_store import AgentSessionStore
from tools.mcp import MCPRuntime
from tools.registry import LocalTool
from tools.tool_gateway import ToolGateway
from workspace import WorkspaceGateway


@dataclass
class Agent:
    """agent 领域唯一公开 facade。"""

    client: OpenAIClient
    workspace_scope: WorkspaceScope
    execution_policy: ExecutionPolicy
    context_policy: ContextPolicy
    permissions: ToolPermissionPolicy
    budget_config: BudgetConfig
    session_paths: SessionPaths
    session_store: AgentSessionStore
    tool_gateway: ToolGateway = field(default_factory=ToolGateway)
    delegation_service: DelegationService = field(default_factory=DelegationService)
    current_agent_id: str | None = None
    progress_reporter: Callable[[JSONDict], None] | None = None
    _context_manager: ContextManager = field(init=False, repr=False)
    _workspace_gateway: WorkspaceGateway = field(init=False, repr=False)
    _mcp_runtime: MCPRuntime = field(init=False, repr=False)
    _result_factory: ResultFactory = field(init=False, repr=False)
    _prompt_processor: PromptProcessor = field(init=False, repr=False)
    _turn_coordinator: TurnCoordinator = field(init=False, repr=False)

    def run(self, prompt: str) -> AgentRunResult:
        """执行一轮端到端任务（新会话）。"""
        self._sync_turn_coordinator()
        run_state = AgentRunState.for_new_session(
            session_state=AgentSessionState(),
            session_id=uuid4().hex,
        )
        result = self._turn_coordinator.run(
            prompt=prompt,
            run_state=run_state,
            resumed_from_session_id=None,
        )
        self.delegation_service = self._turn_coordinator.delegation_service
        return result

    def resume(self, prompt: str, session_snapshot: AgentSessionSnapshot) -> AgentRunResult:
        """从已保存的会话恢复并继续执行新 prompt。"""
        self._sync_turn_coordinator()
        session_state = AgentSessionState.from_persisted(
            messages=list(session_snapshot.messages),
            transcript=list(session_snapshot.transcript),
        )
        run_state = AgentRunState.for_resumed_session(
            session_state=session_state,
            session_id=session_snapshot.session_id,
            turns_offset=session_snapshot.turns,
            usage_baseline=session_snapshot.usage,
            cost_baseline=session_snapshot.total_cost_usd,
            tool_call_count=session_snapshot.tool_calls,
            mcp_capability_shortlist=list(session_snapshot.mcp_capability_shortlist),
            materialized_mcp_capability_handles=list(session_snapshot.materialized_mcp_capability_handles),
        )
        result = self._turn_coordinator.run(
            prompt=prompt,
            run_state=run_state,
            resumed_from_session_id=session_snapshot.session_id,
        )
        self.delegation_service = self._turn_coordinator.delegation_service
        return result

    @property
    def context_manager(self) -> ContextManager:
        """暴露当前 agent 绑定的 context facade。"""
        return self._context_manager

    @property
    def workspace_gateway(self) -> WorkspaceGateway:
        """暴露当前 agent 绑定的 workspace facade。"""
        return self._workspace_gateway

    @property
    def mcp_runtime(self) -> MCPRuntime:
        """暴露当前 agent 绑定的 MCP runtime。"""
        return self._mcp_runtime

    @property
    def tool_registry(self) -> dict[str, LocalTool]:
        """暴露当前基础工具注册表。"""
        return self._turn_coordinator.tool_registry_view()

    @tool_registry.setter
    def tool_registry(self, value: dict[str, LocalTool]) -> None:
        """允许测试与控制面回写基础工具注册表。"""
        self._turn_coordinator.tool_registry = dict(value)

    def _register_workspace_runtime_tools(self, tool_registry: dict[str, LocalTool]) -> dict[str, LocalTool]:
        """代理到 TurnCoordinator 的动态工具注册逻辑。"""
        return self._turn_coordinator._register_workspace_runtime_tools(tool_registry)

    def __post_init__(self) -> None:
        """初始化 agent facade 所需的内部协作者。"""
        self._workspace_gateway = WorkspaceGateway.from_workspace(self.workspace_scope.cwd)
        self._mcp_runtime = MCPRuntime.from_workspace(self.workspace_scope.cwd)
        self.budget_config = self._workspace_gateway.apply_budget_config(self.budget_config)
        self._context_manager = ContextManager(client=self.client)
        self._result_factory = ResultFactory(
            client=self.client,
            workspace_scope=self.workspace_scope,
            execution_policy=self.execution_policy,
            context_policy=self.context_policy,
            permissions=self.permissions,
            budget_config=self.budget_config,
            session_paths=self.session_paths,
            session_store=self.session_store,
        )
        self._turn_coordinator = TurnCoordinator(
            client=self.client,
            workspace_scope=self.workspace_scope,
            execution_policy=self.execution_policy,
            context_policy=self.context_policy,
            permissions=self.permissions,
            budget_config=self.budget_config,
            session_paths=self.session_paths,
            session_store=self.session_store,
            workspace_gateway=self._workspace_gateway,
            mcp_runtime=self._mcp_runtime,
            context_manager=self._context_manager,
            tool_gateway=self.tool_gateway,
            delegation_service=self.delegation_service,
            current_agent_id=self.current_agent_id,
            progress_reporter=self.progress_reporter,
        )
        self._prompt_processor = PromptProcessor(
            slash_dispatcher=SlashCommandDispatcher(self._context_manager),
            workspace_scope=self.workspace_scope,
            context_policy=self.context_policy,
            permissions=self.permissions,
            budget_config=self.budget_config,
            model_config=self.client.model_config,
            workspace_gateway=self._workspace_gateway,
            tool_registry_getter=self._turn_coordinator.tool_registry_view,
            result_factory=self._result_factory,
        )
        self._turn_coordinator.prompt_processor = self._prompt_processor
        self._turn_coordinator.result_factory = self._result_factory
        self._turn_coordinator.child_agent_factory = self._spawn_child_agent

    def _sync_turn_coordinator(self) -> None:
        """在每次 run/resume 前同步可变运行时依赖。"""
        self._turn_coordinator.progress_reporter = self.progress_reporter
        self._turn_coordinator.current_agent_id = self.current_agent_id
        self._turn_coordinator.budget_config = self.budget_config

    def _spawn_child_agent(self, child_agent_id: str) -> 'Agent':
        """构造共享 delegation runtime 的 child agent。"""
        child_agent = Agent(
            self.client,
            self.workspace_scope,
            self.execution_policy,
            self.context_policy,
            self.permissions,
            self.budget_config,
            self.session_paths,
            self.session_store,
            tool_gateway=self.tool_gateway,
            delegation_service=self._turn_coordinator.delegation_service,
            current_agent_id=child_agent_id,
        )
        child_agent.progress_reporter = self.progress_reporter
        return child_agent