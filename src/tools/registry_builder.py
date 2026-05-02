"""负责运行时动态评估并构建工具注册表的建造者。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from core_contracts.tools_contracts import ToolDescriptor, ToolRegistry


class WorkspaceGatewayProvider(Protocol):
    """描述 DynamicRegistryBuilder 对工作区网关的行为需求。"""

    def has_search_providers(self) -> bool: ...

@dataclass(frozen=True)
class DynamicRegistryBuilder:
    """根据当前运行时环境与策略，决定哪些工具被暴露给模型的装配类。"""

    workspace_gateway: WorkspaceGatewayProvider  # WorkspaceGatewayProvider: 注入的外部工作区网关实例。

    def build_extended_registry(
        self,
        base_registry: ToolRegistry,
        handlers: Mapping[str, Callable],
    ) -> ToolRegistry:
        """合并静态基础工具与条件性生成的动态工具。

        Args:
            base_registry: 基础注册表。
            handlers: 将绑定至动态工具的处理器映射。

        Returns:
            新生成的完整注册表。

        Raises:
            ValueError: 缺少必须的 handler 时抛出。
        """
        merged = ToolRegistry.from_mapping(base_registry)
        if self.workspace_gateway.has_search_providers():
            if 'workspace_search' not in handlers:
                raise ValueError("Missing handler for 'workspace_search'")
            merged['workspace_search'] = ToolDescriptor(
                name='workspace_search',
                description='搜索工作区并返回结构化结果。',
                parameters={'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']},
                handler=handlers['workspace_search'],
            )
        return merged
