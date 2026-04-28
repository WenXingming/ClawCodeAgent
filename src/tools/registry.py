"""工具注册表基础模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterator, Mapping

from core_contracts.protocol import JSONDict

if TYPE_CHECKING:
    from tools.executor import ToolExecutionContext, ToolStreamUpdate


ToolHandler = Callable[[JSONDict, 'ToolExecutionContext'], str | tuple[str, JSONDict]]
ToolStreamHandler = Callable[[JSONDict, 'ToolExecutionContext'], Iterator['ToolStreamUpdate']]


@dataclass(frozen=True)
class LocalTool:
    """表示单个可暴露给模型的工具定义。"""

    name: str
    description: str
    parameters: JSONDict
    handler: ToolHandler
    stream_handler: ToolStreamHandler | None = None

    def to_openai_tool(self) -> JSONDict:
        """把工具定义转换为 OpenAI 兼容 schema。"""
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.description,
                'parameters': dict(self.parameters),
            },
        }


def build_registry(*tools: LocalTool) -> dict[str, LocalTool]:
    """按工具对象构建注册表。"""
    return {tool.name: tool for tool in tools}


def merge_tool_registries(*registries: Mapping[str, LocalTool]) -> dict[str, LocalTool]:
    """按顺序合并多个工具注册表。"""
    merged: dict[str, LocalTool] = {}
    for registry in registries:
        merged.update(registry)
    return merged


def render_openai_tools(tool_registry: Mapping[str, LocalTool]) -> list[JSONDict]:
    """把工具注册表投影为模型可见的 schema 列表。"""
    return [tool.to_openai_tool() for tool in tool_registry.values()]