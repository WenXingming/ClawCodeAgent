"""工作区领域统一门面。

本模块定义 workspace 域的唯一跨域入口 `WorkspaceGateway`。
外部调用方应仅通过该网关访问插件、策略、搜索与 worktree 能力，
并使用原生类型或 JSON 契约进行交互，避免依赖内部运行时实现细节。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from core_contracts.config import BudgetConfig
from core_contracts.primitives import JSONDict
from core_contracts.tools_contracts import ToolDescriptor
from workspace.plugin_catalog import PluginCatalog
from workspace.policy_catalog import PolicyCatalog
from workspace.search_service import SearchQueryError, SearchService
from workspace.worktree_service import WorktreeService, WorktreeStatus


class WorkspaceGateway:
    """统一收口工作区插件、策略、搜索与 worktree 能力。

    核心工作流：
    1. `from_workspace` 初始化内部运行时。
    2. `prepare_tool_registry` 合并插件工具并应用策略过滤。
    3. 通过 `search`、`get_before_hooks`、`resolve_block` 等 API 提供运行时能力。
    """

    def __init__(
        self,
        workspace: Path,
        *,
        plugin_catalog: PluginCatalog,
        policy_catalog: PolicyCatalog,
        search_service: SearchService,
        worktree_service: WorktreeService | None,
    ) -> None:
        """初始化工作区网关。
        Args:
            workspace (Path): 工作区根目录。
            plugin_catalog (PluginCatalog): 插件运行时目录。
            policy_catalog (PolicyCatalog): 策略运行时目录。
            search_service (SearchService): 搜索运行时服务。
            worktree_service (WorktreeService | None): worktree 运行时；非 git 仓库时为 None。
        Returns:
            None: 该方法仅负责保存状态。
        Raises:
            ValueError: 当 workspace 不是有效目录时抛出。
        """
        resolved_workspace = workspace.resolve()
        if not resolved_workspace.is_dir():
            raise ValueError(f'Workspace directory does not exist: {resolved_workspace}')
        self._workspace = resolved_workspace  # Path: 网关绑定的工作区根目录。
        self._plugin_catalog = plugin_catalog  # PluginCatalog: 插件清单与注册快照。
        self._policy_catalog = policy_catalog  # PolicyCatalog: 策略合并快照。
        self._search_service = search_service  # SearchService: provider 发现与查询运行时。
        self._worktree_service = worktree_service  # WorktreeService | None: 可选 worktree 运行时。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'WorkspaceGateway':
        """从工作区加载 workspace 领域能力。
        Args:
            workspace (Path): 工作区根目录。
        Returns:
            WorkspaceGateway: 初始化完成的网关实例。
        Raises:
            ValueError: 当工作区路径非法时抛出。
        """
        resolved_workspace = workspace.resolve()
        return cls(
            workspace=resolved_workspace,
            plugin_catalog=PluginCatalog(),
            policy_catalog=PolicyCatalog.from_workspace(resolved_workspace),
            search_service=SearchService.from_workspace(resolved_workspace),
            worktree_service=_load_worktree_service(resolved_workspace),
        )

    def prepare_tool_registry(self, base_tool_registry: Mapping[str, ToolDescriptor]) -> dict[str, ToolDescriptor]:
        """基于插件与策略准备有效工具注册表。
        Args:
            base_tool_registry (Mapping[str, ToolDescriptor]): 基础工具注册表。
        Returns:
            dict[str, ToolDescriptor]: 插件扩展并策略过滤后的工具注册表。
        Raises:
            无。
        """
        self._plugin_catalog = PluginCatalog.from_workspace(self._workspace, base_tool_registry)
        merged_registry = self._plugin_catalog.merge_tool_registry(base_tool_registry)
        return self._policy_catalog.filter_tool_registry(merged_registry)

    def apply_budget_config(self, budget_config: BudgetConfig | None) -> BudgetConfig:
        """把工作区策略预算覆盖应用到基础预算。
        Args:
            budget_config (BudgetConfig | None): 基础预算配置。
        Returns:
            BudgetConfig: 应用覆盖后的预算配置。
        Raises:
            无。
        """
        return self._policy_catalog.apply_budget_config(budget_config)

    def has_search_providers(self) -> bool:
        """判断当前是否存在可用搜索 provider。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            bool: provider 列表非空时返回 True。
        Raises:
            无。
        """
        return bool(self._search_service.providers)

    def list_search_providers(self) -> tuple[JSONDict, ...]:
        """列出搜索 provider 的 JSON 视图。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[JSONDict, ...]: provider 列表。
        Raises:
            无。
        """
        return tuple(provider.to_dict() for provider in self._search_service.list_providers())

    def activate_search_provider(self, provider_id: str) -> JSONDict:
        """激活指定搜索 provider。
        Args:
            provider_id (str): 目标 provider 标识。
        Returns:
            JSONDict: 激活后的 provider JSON。
        Raises:
            ValueError: 当 provider 不存在或参数非法时抛出。
        """
        provider = self._search_service.activate_provider(provider_id)
        return provider.to_dict()

    def search(
        self,
        query: str,
        *,
        provider_id: str | None = None,
        max_results: int | None = None,
        max_retries: int = 0,
    ) -> JSONDict:
        """执行工作区搜索并返回 JSON 契约。
        Args:
            query (str): 查询文本。
            provider_id (str | None): 可选 provider 标识。
            max_results (int | None): 可选最大返回条数。
            max_retries (int): 请求失败后的最大重试次数。
        Returns:
            JSONDict: 搜索响应字典。
        Raises:
            ValueError: 当查询非法或搜索失败时抛出。
        """
        try:
            response = self._search_service.search(
                query,
                provider_id=provider_id,
                max_results=max_results,
                max_retries=max_retries,
            )
        except SearchQueryError as exc:
            raise ValueError(str(exc)) from exc
        return response.to_dict()

    def render_plugin_summary(self) -> str:
        """渲染插件摘要文本。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            str: 插件摘要字符串。
        Raises:
            无。
        """
        return self._plugin_catalog.render_summary()

    def get_before_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        """返回工具调用前 hooks，保持插件优先于策略。
        Args:
            tool_name (str): 工具名称。
        Returns:
            tuple[JSONDict, ...]: before hooks 列表。
        Raises:
            无。
        """
        return self._plugin_catalog.get_before_hooks(tool_name) + self._policy_catalog.get_before_hooks(tool_name)

    def get_after_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        """返回工具调用后 hooks，保持插件优先于策略。
        Args:
            tool_name (str): 工具名称。
        Returns:
            tuple[JSONDict, ...]: after hooks 列表。
        Raises:
            无。
        """
        return self._plugin_catalog.get_after_hooks(tool_name) + self._policy_catalog.get_after_hooks(tool_name)

    def resolve_block(self, tool_name: str) -> JSONDict | None:
        """解析工具阻断决策，保持策略优先于插件。
        Args:
            tool_name (str): 工具名称。
        Returns:
            JSONDict | None: 命中阻断时返回结构化决策，否则返回 None。
        Raises:
            无。
        """
        block_decision = self._policy_catalog.resolve_block(tool_name)
        if block_decision is not None:
            return block_decision
        return self._plugin_catalog.resolve_block(tool_name)

    def has_worktree_runtime(self) -> bool:
        """判断当前工作区是否可用 worktree 运行时。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            bool: worktree 运行时可用时返回 True。
        Raises:
            无。
        """
        return self._worktree_service is not None

    def list_worktrees(self, *, status: str | None = None) -> tuple[JSONDict, ...]:
        """列出受管工作树记录。
        Args:
            status (str | None): 可选状态过滤，支持 active/exited/removed。
        Returns:
            tuple[JSONDict, ...]: 工作树记录 JSON 列表。
        Raises:
            ValueError: 当 worktree 运行时不可用或状态值非法时抛出。
        """
        runtime = self._require_worktree_runtime()
        resolved_status = self._coerce_worktree_status(status)
        records = runtime.list_worktrees(status=resolved_status)
        return tuple(item.to_dict() for item in records)

    def active_worktree(self) -> JSONDict | None:
        """读取当前激活工作树记录。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict | None: 激活工作树记录；不存在时返回 None。
        Raises:
            ValueError: 当 worktree 运行时不可用时抛出。
        """
        runtime = self._require_worktree_runtime()
        record = runtime.active_worktree()
        if record is None:
            return None
        return record.to_dict()

    def enter_worktree(
        self,
        branch: str,
        *,
        path: str | None = None,
        base_ref: str = 'HEAD',
    ) -> JSONDict:
        """创建并进入受管工作树。
        Args:
            branch (str): 目标分支名。
            path (str | None): 可选目标目录。
            base_ref (str): 创建工作树使用的基准引用。
        Returns:
            JSONDict: 新建工作树记录。
        Raises:
            ValueError: 当 worktree 运行时不可用或底层 git 操作失败时抛出。
        """
        runtime = self._require_worktree_runtime()
        resolved_path = Path(path) if path is not None else None
        record = runtime.enter_worktree(branch, path=resolved_path, base_ref=base_ref)
        return record.to_dict()

    def exit_worktree(self, *, remove: bool) -> JSONDict:
        """退出当前激活工作树。
        Args:
            remove (bool): 为 True 时删除底层工作树目录。
        Returns:
            JSONDict: 退出后的工作树记录。
        Raises:
            ValueError: 当 worktree 运行时不可用或退出失败时抛出。
        """
        runtime = self._require_worktree_runtime()
        record = runtime.exit_worktree(remove=remove)
        return record.to_dict()

    def current_worktree_cwd(self) -> str | None:
        """返回 worktree 运行时当前逻辑 cwd。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            str | None: 当前 cwd 字符串；运行时不可用时返回 None。
        Raises:
            无。
        """
        if self._worktree_service is None:
            return None
        return str(self._worktree_service.current_cwd)

    @property
    def safe_env(self) -> dict[str, str]:
        """返回策略提供的安全环境变量映射。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            dict[str, str]: 安全环境变量副本。
        Raises:
            无。
        """
        return dict(self._policy_catalog.safe_env)

    @property
    def plugin_count(self) -> int:
        """返回已加载插件数量。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            int: 插件数量。
        Raises:
            无。
        """
        return len(self._plugin_catalog.manifests)

    @property
    def policy_count(self) -> int:
        """返回已加载策略数量。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            int: 策略数量。
        Raises:
            无。
        """
        return len(self._policy_catalog.manifests)

    @property
    def search_provider_count(self) -> int:
        """返回已加载搜索 provider 数量。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            int: provider 数量。
        Raises:
            无。
        """
        return len(self._search_service.providers)

    @property
    def load_error_count(self) -> int:
        """返回 workspace 领域累计加载错误数量。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            int: 插件、策略与搜索加载错误总数。
        Raises:
            无。
        """
        return (
            len(self._plugin_catalog.load_errors)
            + len(self._policy_catalog.load_errors)
            + len(self._search_service.load_errors)
        )

    def _coerce_worktree_status(self, status: str | None) -> WorktreeStatus | None:
        """把可选状态字符串转换为 WorktreeStatus。
        Args:
            status (str | None): 状态字符串。
        Returns:
            WorktreeStatus | None: 转换后的状态；输入为空时返回 None。
        Raises:
            ValueError: 当状态值不在允许集合内时抛出。
        """
        if status is None:
            return None
        normalized_status = str(status).strip().lower()
        if not normalized_status:
            return None
        try:
            return WorktreeStatus(normalized_status)
        except ValueError as exc:
            raise ValueError(f'Invalid worktree status: {status!r}') from exc

    def _require_worktree_runtime(self) -> WorktreeService:
        """返回可用 worktree 运行时，不可用则抛出统一错误。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            WorktreeService: 可用 worktree 运行时。
        Raises:
            ValueError: 当当前工作区不支持 worktree 运行时抛出。
        """
        if self._worktree_service is None:
            raise ValueError('Worktree runtime is unavailable in this workspace')
        return self._worktree_service


def _load_worktree_service(workspace: Path) -> WorktreeService | None:
    """按工作区尝试加载 worktree 服务；非 git 仓库时返回 None。
    Args:
        workspace (Path): 工作区根目录。
    Returns:
        WorktreeService | None: 可用服务实例；不可用时返回 None。
    Raises:
        无。
    """
    try:
        return WorktreeService.from_workspace(workspace)
    except ValueError:
        return None

