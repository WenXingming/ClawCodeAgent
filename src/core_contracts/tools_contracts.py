"""tools 领域跨模块共享契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, Mapping

from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict, ToolExecutionResult

if TYPE_CHECKING:
    from core_contracts.runtime_policy import ExecutionPolicy, WorkspaceScope


ToolHandler = Callable[[JSONDict, 'ToolExecutionContext'], str | tuple[str, JSONDict]]
ToolStreamHandler = Callable[[JSONDict, 'ToolExecutionContext'], Iterator['ToolStreamUpdate']]


@dataclass(frozen=True)
class ToolDescriptor:
    """可暴露给模型的工具契约。"""

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


@dataclass(frozen=True)
class ToolExecutionContext:
    """一次工具执行共享的不可变上下文。"""

    root: Path
    command_timeout_seconds: float
    max_output_chars: int
    permissions: ToolPermissionPolicy
    safe_env: dict[str, str] = field(default_factory=dict)
    tool_registry: Mapping[str, ToolDescriptor] | None = None


@dataclass(frozen=True)
class ToolStreamUpdate:
    """流式工具调用过程中产出的单条更新。"""

    kind: str
    chunk: str = ''
    result: ToolExecutionResult | None = None
    metadata: JSONDict = field(default_factory=dict)


def build_execution_context(
    workspace_scope: 'WorkspaceScope',
    execution_policy: 'ExecutionPolicy',
    permissions: ToolPermissionPolicy,
    *,
    tool_registry: Mapping[str, ToolDescriptor] | None = None,
    safe_env: dict[str, str] | None = None,
) -> ToolExecutionContext:
    """按运行时配置构造工具上下文契约对象。"""
    return ToolExecutionContext(
        root=workspace_scope.cwd.resolve(),
        command_timeout_seconds=execution_policy.command_timeout_seconds,
        max_output_chars=execution_policy.max_output_chars,
        permissions=permissions,
        safe_env=dict(safe_env or {}),
        tool_registry=tool_registry,
    )
