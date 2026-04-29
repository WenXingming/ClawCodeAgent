"""工作区领域统一门面。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from core_contracts.budget import BudgetConfig
from core_contracts.protocol import JSONDict
from core_contracts.tools_contracts import ToolDescriptor
from workspace.plugin_catalog import PluginCatalog
from workspace.policy_catalog import PolicyCatalog
from workspace.search_service import SearchResponse, SearchService
from workspace.worktree_service import WorktreeService


@dataclass
class WorkspaceGateway:
    """统一收口工作区插件、策略、搜索与 worktree 能力。"""

    workspace: Path
    plugin_catalog: PluginCatalog = field(default_factory=PluginCatalog)
    policy_catalog: PolicyCatalog = field(default_factory=PolicyCatalog)
    search_service: SearchService = field(default_factory=SearchService)
    worktree_service: WorktreeService | None = None

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'WorkspaceGateway':
        """从工作区加载 workspace 领域能力。"""
        resolved_workspace = workspace.resolve()
        return cls(
            workspace=resolved_workspace,
            plugin_catalog=PluginCatalog(),
            policy_catalog=PolicyCatalog.from_workspace(resolved_workspace),
            search_service=SearchService.from_workspace(resolved_workspace),
            worktree_service=_load_worktree_service(resolved_workspace),
        )

    def prepare_tool_registry(self, base_tool_registry: Mapping[str, ToolDescriptor]) -> dict[str, ToolDescriptor]:
        """基于当前工作区插件与策略准备有效工具注册表。"""
        self.plugin_catalog = PluginCatalog.from_workspace(self.workspace, base_tool_registry)
        merged_registry = self.plugin_catalog.merge_tool_registry(base_tool_registry)
        return self.policy_catalog.filter_tool_registry(merged_registry)

    def apply_budget_config(self, budget_config: BudgetConfig) -> BudgetConfig:
        """把工作区策略中的预算覆盖应用到基础预算配置。"""
        return self.policy_catalog.apply_budget_config(budget_config)

    def has_search_providers(self) -> bool:
        """返回当前工作区是否存在可用搜索 provider。"""
        return bool(self.search_service.providers)

    def search(
        self,
        query: str,
        *,
        provider_id: str | None = None,
        max_results: int | None = None,
        max_retries: int = 0,
    ) -> SearchResponse:
        """执行一次工作区搜索。"""
        return self.search_service.search(
            query,
            provider_id=provider_id,
            max_results=max_results,
            max_retries=max_retries,
        )

    def render_plugin_summary(self) -> str:
        """渲染插件摘要文本。"""
        return self.plugin_catalog.render_summary()

    def get_before_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        """返回工具调用前的全部 hook，保持插件优先于策略的顺序。"""
        return self.plugin_catalog.get_before_hooks(tool_name) + self.policy_catalog.get_before_hooks(tool_name)

    def get_after_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        """返回工具调用后的全部 hook，保持插件优先于策略的顺序。"""
        return self.plugin_catalog.get_after_hooks(tool_name) + self.policy_catalog.get_after_hooks(tool_name)

    def resolve_block(self, tool_name: str) -> JSONDict | None:
        """解析工具阻断决策，保持策略优先于插件的顺序。"""
        block_decision = self.policy_catalog.resolve_block(tool_name)
        if block_decision is not None:
            return block_decision
        return self.plugin_catalog.resolve_block(tool_name)

    @property
    def safe_env(self) -> dict[str, str]:
        """返回工作区策略提供的安全环境变量映射。"""
        return dict(self.policy_catalog.safe_env)

    @property
    def plugin_count(self) -> int:
        """返回已加载插件数量。"""
        return len(self.plugin_catalog.manifests)

    @property
    def policy_count(self) -> int:
        """返回已加载策略数量。"""
        return len(self.policy_catalog.manifests)

    @property
    def search_provider_count(self) -> int:
        """返回已加载搜索 provider 数量。"""
        return len(self.search_service.providers)

    @property
    def load_error_count(self) -> int:
        """返回 workspace 领域累计的加载错误数量。"""
        return (
            len(self.plugin_catalog.load_errors)
            + len(self.policy_catalog.load_errors)
            + len(self.search_service.load_errors)
        )


def _load_worktree_service(workspace: Path) -> WorktreeService | None:
    """按工作区尝试加载 worktree 服务；非 git 仓库时返回 None。"""
    try:
        return WorktreeService.from_workspace(workspace)
    except ValueError:
        return None