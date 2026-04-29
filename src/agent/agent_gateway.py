"""agent 领域网关与唯一公开实现。

本文件定义 agent 文件夹的唯一公开门面类型 AgentGateway。
外部调用方必须通过该网关访问 agent 运行能力，禁止直接依赖
agent 文件夹内的其他内部实现模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from agent.delegation_service import DelegationService
from agent.prompt_processor import PromptProcessor
from agent.result_factory import ResultFactory
from agent.run_state import AgentRunState
from agent.turn_coordinator import TurnCoordinator
from context.context_gateway import ContextGateway
from core_contracts.config import BudgetConfig
from core_contracts.config import ContextPolicy, ExecutionPolicy, SessionPaths, ToolPermissionPolicy, WorkspaceScope
from core_contracts.model import ModelClient
from core_contracts.outcomes import AgentRunResult
from core_contracts.primitives import JSONDict
from core_contracts.session import AgentSessionSnapshot, AgentSessionState
from core_contracts.tools import ToolDescriptor
from interaction.interaction_gateway import SlashCommandDispatcher
from session.session_gateway import SessionGateway
from tools.tools_gateway import ToolsGateway
from workspace import WorkspaceGateway


@dataclass
class AgentGateway:
    """agent 领域唯一公开 Gateway。

    核心工作流：
    1. 初始化 workspace/context/tools/session 相关协作者。
    2. run() 创建新会话运行态并交给 TurnCoordinator。
    3. resume() 基于快照恢复运行态并继续执行。
    4. child agent 通过同一网关类型递归创建，复用 delegation runtime。
    """

    client: ModelClient  # ModelClient: 模型调用网关。
    workspace_scope: WorkspaceScope  # WorkspaceScope: 当前工作区路径与搜索目录范围。
    execution_policy: ExecutionPolicy  # ExecutionPolicy: 最大轮次与命令执行限制。
    context_policy: ContextPolicy  # ContextPolicy: 上下文治理策略。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy: 文件写入与 shell 权限控制。
    session_paths: SessionPaths  # SessionPaths: session 与 scratchpad 的持久化路径。
    session_manager: SessionGateway  # SessionGateway: 会话快照存取门面。
    budget_config: BudgetConfig | None = None  # BudgetConfig | None: 预算阈值配置，可被工作区覆盖。
    tool_gateway: ToolsGateway = field(default_factory=ToolsGateway)  # ToolsGateway: 工具注册与执行门面。
    delegation_service: DelegationService = field(default_factory=DelegationService)  # DelegationService: 子代理委派与分组状态。
    current_agent_id: str | None = None  # str | None: 当前调用树中的受管代理 ID。
    progress_reporter: Callable[[JSONDict], None] | None = None  # Callable[[JSONDict], None] | None: 实时事件上报回调。
    _context_gateway: ContextGateway = field(init=False, repr=False)  # ContextGateway: 上下文预算和压缩门面。
    _workspace_gateway: WorkspaceGateway = field(init=False, repr=False)  # WorkspaceGateway: 工作区策略、hook 与搜索门面。
    _result_factory: ResultFactory = field(init=False, repr=False)  # ResultFactory: 最终结果与会话快照构建器。
    _prompt_processor: PromptProcessor = field(init=False, repr=False)  # PromptProcessor: slash 命令和 prompt 预处理器。
    _turn_coordinator: TurnCoordinator = field(init=False, repr=False)  # TurnCoordinator: 单轮主循环编排器。

    def run(self, prompt: str) -> AgentRunResult:
        """执行一轮端到端任务（新会话）。
        Args:
            prompt (str): 本轮用户输入。
        Returns:
            AgentRunResult: 当前调用的最终结果对象。
        Raises:
            Exception: 主循环内部未处理的异常会向上透传。
        """
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
        """从已保存会话恢复并继续执行新 prompt。
        Args:
            prompt (str): 本轮用户输入。
            session_snapshot (AgentSessionSnapshot): 待恢复的会话快照。
        Returns:
            AgentRunResult: 当前调用的最终结果对象。
        Raises:
            Exception: 主循环内部未处理的异常会向上透传。
        """
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
    def context_gateway(self) -> ContextGateway:
        """返回当前绑定的 context gateway。
        Args:
            无。
        Returns:
            ContextGateway: 当前运行时的上下文门面。
        Raises:
            无。
        """
        return self._context_gateway

    @property
    def context_manager(self) -> ContextGateway:
        """返回当前绑定的 context facade。
        Args:
            无。
        Returns:
            ContextGateway: 当前运行时的上下文门面。
        Raises:
            无。
        """
        return self._context_gateway

    @property
    def workspace_gateway(self) -> WorkspaceGateway:
        """返回当前绑定的 workspace facade。
        Args:
            无。
        Returns:
            WorkspaceGateway: 当前运行时的工作区门面。
        Raises:
            无。
        """
        return self._workspace_gateway

    @property
    def tool_registry(self) -> dict[str, ToolDescriptor]:
        """返回当前基础工具注册表。
        Args:
            无。
        Returns:
            dict[str, ToolDescriptor]: 工具名到描述符的映射副本。
        Raises:
            无。
        """
        return self._turn_coordinator.tool_registry_view()

    @tool_registry.setter
    def tool_registry(self, value: dict[str, ToolDescriptor]) -> None:
        """回写当前基础工具注册表。
        Args:
            value (dict[str, ToolDescriptor]): 需要替换的工具注册表。
        Returns:
            None: 原地更新运行时注册表。
        Raises:
            无。
        """
        self._turn_coordinator.tool_registry = dict(value)

    @property
    def mcp_runtime(self):
        """返回当前绑定的 MCP runtime。
        Args:
            无。
        Returns:
            Any: tool gateway 内部维护的 MCP runtime 对象。
        Raises:
            无。
        """
        return self.tool_gateway._mcp_runtime

    def _register_workspace_runtime_tools(self, tool_registry: dict[str, ToolDescriptor]) -> dict[str, ToolDescriptor]:
        """代理到 TurnCoordinator 的动态工具注册逻辑。
        Args:
            tool_registry (dict[str, ToolDescriptor]): 待扩展的工具注册表。
        Returns:
            dict[str, ToolDescriptor]: 已追加动态工具后的注册表。
        Raises:
            Exception: 底层工具注册过程异常会向上透传。
        """
        return self._turn_coordinator._register_workspace_runtime_tools(tool_registry)

    def __post_init__(self) -> None:
        """初始化运行时内部协作者。
        Args:
            无。
        Returns:
            None: 原地完成运行时装配。
        Raises:
            Exception: 任一协作者构造失败时向上透传。
        """
        self._workspace_gateway = WorkspaceGateway.from_workspace(self.workspace_scope.cwd)
        self.tool_gateway.bind_workspace(self.workspace_scope.cwd)
        self.budget_config = self._workspace_gateway.apply_budget_config(self.budget_config)
        self._context_gateway = ContextGateway(client=self.client)
        self._result_factory = ResultFactory(
            client=self.client,
            workspace_scope=self.workspace_scope,
            execution_policy=self.execution_policy,
            context_policy=self.context_policy,
            permissions=self.permissions,
            budget_config=self.budget_config,
            session_paths=self.session_paths,
            session_manager=self.session_manager,
        )
        self._turn_coordinator = TurnCoordinator(
            client=self.client,
            workspace_scope=self.workspace_scope,
            execution_policy=self.execution_policy,
            context_policy=self.context_policy,
            permissions=self.permissions,
            budget_config=self.budget_config,
            session_paths=self.session_paths,
            session_manager=self.session_manager,
            workspace_gateway=self._workspace_gateway,
            context_manager=self._context_gateway,
            tool_gateway=self.tool_gateway,
            delegation_service=self.delegation_service,
            current_agent_id=self.current_agent_id,
            progress_reporter=self.progress_reporter,
        )
        self._prompt_processor = PromptProcessor(
            slash_dispatcher=SlashCommandDispatcher(self._context_gateway),
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
        """在每次 run/resume 前同步可变运行时依赖。
        Args:
            无。
        Returns:
            None: 原地同步 coordinator 状态。
        Raises:
            无。
        """
        self._turn_coordinator.progress_reporter = self.progress_reporter
        self._turn_coordinator.current_agent_id = self.current_agent_id
        self._turn_coordinator.budget_config = self.budget_config

    def _spawn_child_agent(self, child_agent_id: str) -> 'AgentGateway':
        """构造共享 delegation runtime 的 child agent。
        Args:
            child_agent_id (str): 新 child agent 的受管标识。
        Returns:
            AgentGateway: 已完成装配的子代理网关实例。
        Raises:
            Exception: 子代理构造失败时向上透传。
        """
        child_agent = AgentGateway(
            self.client,
            self.workspace_scope,
            self.execution_policy,
            self.context_policy,
            self.permissions,
            self.session_paths,
            self.session_manager,
            self.budget_config,
            tool_gateway=self.tool_gateway,
            delegation_service=self._turn_coordinator.delegation_service,
            current_agent_id=child_agent_id,
        )
        child_agent.progress_reporter = self.progress_reporter
        return child_agent
