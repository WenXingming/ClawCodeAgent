"""负责运行时动态评估并构建工具注册表的建造者。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from core_contracts.primitives import JSONDict
from core_contracts.tools_contracts import ToolDescriptor


class WorkspaceGatewayProvider(Protocol):
    """描述 DynamicRegistryBuilder 对工作区网关的行为需求。"""

    def has_search_providers(self) -> bool: ...


def build_registry(*tools: ToolDescriptor) -> dict[str, ToolDescriptor]:
    """按工具对象构建注册表。

    Args:
        *tools: 零个或多个工具描述符。

    Returns:
        以工具名为键的注册表字典。
    """
    return {tool.name: tool for tool in tools}


def merge_tool_registries(*registries: Mapping[str, ToolDescriptor]) -> dict[str, ToolDescriptor]:
    """按顺序合并多个工具注册表。

    Args:
        *registries: 零个或多个待合并的注册表。

    Returns:
        合并后的注册表，后出现的覆盖先出现的同名工具。
    """
    merged: dict[str, ToolDescriptor] = {}
    for registry in registries:
        merged.update(registry)
    return merged


def render_openai_tools(tool_registry: Mapping[str, ToolDescriptor]) -> list[JSONDict]:
    """把工具注册表投影为模型可见的 schema 列表。

    Args:
        tool_registry: 当前可见工具注册表。

    Returns:
        OpenAI 兼容工具声明列表。
    """
    return [tool.to_openai_tool() for tool in tool_registry.values()]


@dataclass(frozen=True)
class DynamicRegistryBuilder:
    """根据当前运行时环境与策略，决定哪些工具被暴露给模型的装配类。"""

    workspace_gateway: WorkspaceGatewayProvider  # WorkspaceGatewayProvider: 注入的外部工作区网关实例。

    def build_extended_registry(
        self,
        base_registry: Mapping[str, ToolDescriptor],
        handlers: Mapping[str, Callable],
    ) -> dict[str, ToolDescriptor]:
        """合并静态基础工具与条件性生成的动态工具。

        Args:
            base_registry: 基础注册表。
            handlers: 将绑定至动态工具的处理器映射。

        Returns:
            新生成的完整注册表。

        Raises:
            ValueError: 缺少必须的 handler 时抛出。
        """
        merged = dict(base_registry)
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
