"""工具执行契约。

定义 ToolDescriptor、ToolExecutionContext、ToolStreamUpdate、ToolExecutionRequest
及 Handler 类型别名。ToolExecutionContext 通过 build 静态工厂方法构造。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, Mapping, MutableMapping, Optional

from core_contracts._coercion import _as_bool, _as_dict, _first_present
from core_contracts.errors import GatewayError, GatewayRuntimeError
from core_contracts.messaging import ToolExecutionResult
from core_contracts.primitives import JSONDict

if TYPE_CHECKING:
    from .config import ExecutionPolicy, WorkspaceScope


ToolHandler = Callable[[JSONDict, 'ToolExecutionContext'], str | tuple[str, JSONDict]]
ToolStreamHandler = Callable[[JSONDict, 'ToolExecutionContext'], Iterator['ToolStreamUpdate']]


@dataclass(frozen=True)
class ToolPermissionPolicy:
    """运行时和工具执行使用的权限开关。"""

    allow_file_write: bool = False  # bool：是否允许写文件。
    allow_shell_commands: bool = False  # bool：是否允许执行 shell 命令。
    allow_destructive_shell_commands: bool = False  # bool：是否允许破坏性 shell 命令。

    def to_dict(self) -> JSONDict:
        """把权限策略序列化为字典。"""
        return {
            'allow_file_write': self.allow_file_write,
            'allow_shell_commands': self.allow_shell_commands,
            'allow_destructive_shell_commands': self.allow_destructive_shell_commands,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ToolPermissionPolicy':
        """从 JSON 字典恢复权限策略配置。"""
        data = _as_dict(payload)
        return cls(
            allow_file_write=_as_bool(_first_present(data, 'allow_file_write', 'allowFileWrite'), False),
            allow_shell_commands=_as_bool(_first_present(data, 'allow_shell_commands', 'allowShellCommands'), False),
            allow_destructive_shell_commands=_as_bool(
                _first_present(data, 'allow_destructive_shell_commands', 'allowDestructiveShellCommands'), False
            ),
        )


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


@dataclass
class ToolRegistry(MutableMapping[str, ToolDescriptor]):
    """工具注册表值对象。"""

    _items: dict[str, ToolDescriptor]

    @classmethod
    def from_tools(cls, *tools: ToolDescriptor) -> 'ToolRegistry':
        """按工具列表构建注册表。"""
        return cls({tool.name: tool for tool in tools})

    @classmethod
    def from_mapping(cls, registry: Mapping[str, ToolDescriptor]) -> 'ToolRegistry':
        """从任意映射构建注册表对象。"""
        if isinstance(registry, ToolRegistry):
            return registry
        return cls(dict(registry))

    def merged_with(self, *registries: Mapping[str, ToolDescriptor]) -> 'ToolRegistry':
        """按顺序合并其他注册表，后者覆盖前者。"""
        merged: dict[str, ToolDescriptor] = dict(self._items)
        for registry in registries:
            merged.update(dict(registry))
        return ToolRegistry(merged)

    def to_openai_tools(self) -> list[JSONDict]:
        """将注册表投影为 OpenAI 兼容工具声明列表。"""
        return [tool.to_openai_tool() for tool in self._items.values()]

    def as_dict(self) -> dict[str, ToolDescriptor]:
        """返回可变字典副本。"""
        return dict(self._items)

    def __getitem__(self, key: str) -> ToolDescriptor:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __setitem__(self, key: str, value: ToolDescriptor) -> None:
        self._items[key] = value

    def __delitem__(self, key: str) -> None:
        del self._items[key]


@dataclass(frozen=True)
class ToolExecutionContext:
    """一次工具执行共享的不可变上下文。

    通过 build 静态工厂方法从运行时配置构造实例。
    """

    root: Path  # Path：工作区根目录。
    command_timeout_seconds: float  # float：命令超时时间，单位秒。
    max_output_chars: int  # int：输出最大字符数。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy：权限策略。
    safe_env: dict[str, str] = field(default_factory=dict)  # dict[str, str]：安全环境变量。
    tool_registry: ToolRegistry | None = None  # ToolRegistry | None：工具注册表。

    @staticmethod
    def build(
        workspace_scope: 'WorkspaceScope',
        execution_policy: 'ExecutionPolicy',
        permissions: ToolPermissionPolicy,
        *,
        tool_registry: ToolRegistry | None = None,
        safe_env: dict[str, str] | None = None,
    ) -> 'ToolExecutionContext':
        """按运行时配置构造工具上下文契约对象。

        Args:
            workspace_scope: 工作区路径与运行目录约束。
            execution_policy: 执行超时与输出预算配置。
            permissions: 工具权限策略。
            tool_registry: 可选工具注册表。
            safe_env: 可选安全环境变量覆盖。

        Returns:
            供工具处理器消费的不可变上下文。
        """
        return ToolExecutionContext(
            root=workspace_scope.cwd.resolve(),
            command_timeout_seconds=execution_policy.command_timeout_seconds,
            max_output_chars=execution_policy.max_output_chars,
            permissions=permissions,
            safe_env=dict(safe_env or {}),
            tool_registry=tool_registry,
        )


@dataclass(frozen=True)
class ToolStreamUpdate:
    """流式工具调用过程中产出的单条更新。"""

    kind: str  # str：更新类型（stdout / stderr / result）。
    chunk: str = ''  # str：流式文本片段。
    result: ToolExecutionResult | None = None  # ToolExecutionResult | None：最终结果。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict：额外元数据。




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

