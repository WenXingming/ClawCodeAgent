"""工具执行契约。

从 tools_contracts.py 简化命名，定义 ToolDescriptor、ToolExecutionContext、
ToolStreamUpdate 及 Handler 类型别名，外加工厂函数 build_execution_context。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, Mapping, Optional

from core_contracts.config import ToolPermissionPolicy
from core_contracts.errors import GatewayError, GatewayRuntimeError
from core_contracts.messaging import ToolExecutionResult
from core_contracts.primitives import JSONDict

if TYPE_CHECKING:
    from .config import ExecutionPolicy, WorkspaceScope


ToolHandler = Callable[[JSONDict, 'ToolExecutionContext'], str | tuple[str, JSONDict]]
ToolStreamHandler = Callable[[JSONDict, 'ToolExecutionContext'], Iterator['ToolStreamUpdate']]


@dataclass(frozen=True)
class ToolDescriptor:
    """可暴露给模型的工具契约。"""

    name: str  # str：工具名称。
    description: str  # str：工具用途说明。
    parameters: JSONDict  # JSONDict：工具输入参数 JSON Schema。
    handler: ToolHandler  # ToolHandler：普通执行入口。
    stream_handler: ToolStreamHandler | None = None  # ToolStreamHandler | None：流式执行入口。

    def to_openai_tool(self) -> JSONDict:
        """把工具定义转换为 OpenAI 兼容 schema。
        Returns:
            JSONDict: OpenAI function 声明字典。
        """
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

    root: Path  # Path：工作区根目录。
    command_timeout_seconds: float  # float：命令超时时间，单位秒。
    max_output_chars: int  # int：输出最大字符数。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy：权限策略。
    safe_env: dict[str, str] = field(default_factory=dict)  # dict[str, str]：安全环境变量。
    tool_registry: Mapping[str, ToolDescriptor] | None = None  # Mapping | None：工具注册表。


@dataclass(frozen=True)
class ToolStreamUpdate:
    """流式工具调用过程中产出的单条更新。"""

    kind: str  # str：更新类型（stdout / stderr / result）。
    chunk: str = ''  # str：流式文本片段。
    result: ToolExecutionResult | None = None  # ToolExecutionResult | None：最终结果。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict：额外元数据。


def build_execution_context(
    workspace_scope: 'WorkspaceScope',
    execution_policy: 'ExecutionPolicy',
    permissions: ToolPermissionPolicy,
    *,
    tool_registry: Mapping[str, ToolDescriptor] | None = None,
    safe_env: dict[str, str] | None = None,
) -> ToolExecutionContext:
    """按运行时配置构造工具上下文契约对象。
    Args:
        workspace_scope (WorkspaceScope): 工作区路径与运行目录约束。
        execution_policy (ExecutionPolicy): 执行超时与输出预算配置。
        permissions (ToolPermissionPolicy): 工具权限策略。
        tool_registry (Mapping[str, ToolDescriptor] | None): 可选工具注册表。
        safe_env (dict[str, str] | None): 可选安全环境变量覆盖。
    Returns:
        ToolExecutionContext: 供工具处理器消费的不可变上下文。
    """
    return ToolExecutionContext(
        root=workspace_scope.cwd.resolve(),
        command_timeout_seconds=execution_policy.command_timeout_seconds,
        max_output_chars=execution_policy.max_output_chars,
        permissions=permissions,
        safe_env=dict(safe_env or {}),
        tool_registry=tool_registry,
    )


class ToolsGatewayError(GatewayError):
    """tools 模块的基础统一异常。"""


class ToolsExecutionError(GatewayRuntimeError):
    """tools 模块执行期的统一运行时异常。"""


@dataclass(frozen=True)
class ToolExecutionRequest:
    """标准化的工具执行请求契约。"""

    tool_name: str
    arguments: JSONDict
    context: ToolExecutionContext
    server_name: Optional[str] = None
    max_chars: int = 12000


@dataclass(frozen=True)
class McpResourceQuery:
    """MCP 资源查询请求契约。"""

    query: Optional[str] = None
    server_name: Optional[str] = None
    limit: int = 100


@dataclass(frozen=True)
class McpCapabilityQuery:
    """MCP 能力查询请求契约。"""

    query: Optional[str] = None
    server_name: Optional[str] = None
    limit: int = 100

