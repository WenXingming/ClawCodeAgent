"""工具注册表基础模型。"""

from __future__ import annotations

from typing import Mapping

from core_contracts.tools_contracts import ToolDescriptor
from core_contracts.protocol import JSONDict

# Internal alias retained inside tools domain to minimize churn.
LocalTool = ToolDescriptor


def build_registry(*tools: ToolDescriptor) -> dict[str, ToolDescriptor]:
    """按工具对象构建注册表。"""
    return {tool.name: tool for tool in tools}


def merge_tool_registries(*registries: Mapping[str, ToolDescriptor]) -> dict[str, ToolDescriptor]:
    """按顺序合并多个工具注册表。"""
    merged: dict[str, ToolDescriptor] = {}
    for registry in registries:
        merged.update(registry)
    return merged


def render_openai_tools(tool_registry: Mapping[str, ToolDescriptor]) -> list[JSONDict]:
    """把工具注册表投影为模型可见的 schema 列表。"""
    return [tool.to_openai_tool() for tool in tool_registry.values()]