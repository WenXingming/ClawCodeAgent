"""工具注册表基础模型。

提供工具注册表的构建、合并与模型投影功能。
所有函数以 ToolDescriptor 为核心操作对象，不依赖外部运行状态。
"""

from __future__ import annotations

from typing import Mapping

from core_contracts.primitives import JSONDict
from core_contracts.tools_contracts import ToolDescriptor


def build_registry(*tools: ToolDescriptor) -> dict[str, ToolDescriptor]:
    """按工具对象构建注册表。
    Args:
        *tools (ToolDescriptor): 零个或多个工具描述符。
    Returns:
        dict[str, ToolDescriptor]: 以工具名为键的注册表字典。
    """
    return {tool.name: tool for tool in tools}


def merge_tool_registries(*registries: Mapping[str, ToolDescriptor]) -> dict[str, ToolDescriptor]:
    """按顺序合并多个工具注册表。
    Args:
        *registries (Mapping[str, ToolDescriptor]): 零个或多个待合并的注册表。
    Returns:
        dict[str, ToolDescriptor]: 合并后的注册表，后出现的覆盖先出现的同名工具。
    """
    merged: dict[str, ToolDescriptor] = {}
    for registry in registries:
        merged.update(registry)
    return merged


def render_openai_tools(tool_registry: Mapping[str, ToolDescriptor]) -> list[JSONDict]:
    """把工具注册表投影为模型可见的 schema 列表。
    Args:
        tool_registry (Mapping[str, ToolDescriptor]): 当前可见工具注册表。
    Returns:
        list[JSONDict]: OpenAI 兼容工具声明列表。
    """
    return [tool.to_openai_tool() for tool in tool_registry.values()]

