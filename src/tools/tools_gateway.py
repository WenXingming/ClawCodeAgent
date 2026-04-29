"""tools 领域统一网关。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Mapping

from core_contracts.gateway_errors import (
    GatewayNotFoundError,
    GatewayPermissionError,
    GatewayRuntimeError,
    GatewayTransportError,
    GatewayValidationError,
)
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict, ToolExecutionResult
from core_contracts.runtime_policy import ExecutionPolicy, WorkspaceScope
from core_contracts.tools_contracts import ToolDescriptor, ToolExecutionContext, ToolStreamUpdate
from tools.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutionContext as _InternalToolExecutionContext
from tools.executor import ToolExecutionError, ToolExecutor, ToolPermissionError
from tools.local.filesystem_tools import build_filesystem_tools
from tools.local.shell_tools import build_shell_tool
from tools.mcp import MCPRuntime, MCPTool, MCPTransportError
from tools.registry import build_registry, render_openai_tools


@dataclass
class ToolsGateway:
    """tools 领域唯一对外入口。"""

    shell_security_policy: ShellSecurityPolicy = field(default_factory=ShellSecurityPolicy)
    _executor: ToolExecutor = field(default_factory=ToolExecutor)
    _mcp_runtime: MCPRuntime | None = field(default=None, init=False, repr=False)
    _workspace: Path | None = field(default=None, init=False, repr=False)

    def bind_workspace(self, workspace: Path) -> None:
        """绑定工作区并初始化 MCP 运行时。"""
        resolved = workspace.resolve()
        if self._workspace == resolved and self._mcp_runtime is not None:
            return
        self._workspace = resolved
        self._mcp_runtime = MCPRuntime.from_workspace(resolved)

    def default_registry(self) -> dict[str, ToolDescriptor]:
        """返回内置基础工具注册表。"""
        return build_registry(
            *build_filesystem_tools(),
            build_shell_tool(self.shell_security_policy),
        )

    def build_context(
        self,
        workspace_scope: WorkspaceScope,
        execution_policy: ExecutionPolicy,
        permissions: ToolPermissionPolicy,
        *,
        tool_registry: Mapping[str, ToolDescriptor] | None = None,
        safe_env: dict[str, str] | None = None,
    ) -> ToolExecutionContext:
        """根据运行时配置构造工具执行上下文。"""
        context = self._executor.build_context(
            workspace_scope,
            execution_policy,
            permissions,
            tool_registry=tool_registry,
            safe_env=safe_env,
        )
        if not isinstance(context, _InternalToolExecutionContext):
            return context
        return ToolExecutionContext(
            root=context.root,
            command_timeout_seconds=context.command_timeout_seconds,
            max_output_chars=context.max_output_chars,
            permissions=context.permissions,
            safe_env=dict(context.safe_env),
            tool_registry=context.tool_registry,
        )

    def execute(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """执行一次工具调用。"""
        return self._executor.execute(tool_registry, name, arguments, context)

    def execute_streaming(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """执行一次流式工具调用。"""
        return self._executor.execute_streaming(tool_registry, name, arguments, context)

    def execute_call(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
        *,
        on_stream_update: Callable[[ToolStreamUpdate], None] | None = None,
    ) -> ToolExecutionResult:
        """执行一次工具调用，并在流式片段出现时回调上报。"""
        final_result: ToolExecutionResult | None = None
        for update in self.execute_streaming(tool_registry, name, arguments, context):
            if update.kind == 'result':
                final_result = update.result
                continue
            if on_stream_update is not None:
                on_stream_update(update)

        if final_result is not None:
            return final_result
        return ToolExecutionResult(
            name=name,
            ok=False,
            content='Streaming tool execution returned no final result.',
            metadata={'error_kind': 'tool_execution_error'},
        )

    def to_openai_tools(self, tool_registry: Mapping[str, ToolDescriptor]) -> list[JSONDict]:
        """把工具注册表投影为模型可见 schema 列表。"""
        return render_openai_tools(tool_registry)

    @property
    def mcp_runtime(self) -> MCPRuntime:
        """返回已绑定工作区的 MCP runtime。"""
        if self._mcp_runtime is None:
            raise GatewayRuntimeError('ToolsGateway is not bound to a workspace yet')
        return self._mcp_runtime

    def has_mcp_servers(self) -> bool:
        """当前工作区是否存在可用 MCP 服务器。"""
        return bool(self.mcp_runtime.servers)

    def has_mcp_resources(self) -> bool:
        """当前工作区是否存在可见 MCP 资源。"""
        return bool(self.mcp_runtime.resources)

    def mcp_list_resources(self, *, query: str | None, server_name: str | None, limit: int) -> tuple[object, ...]:
        """列出 MCP 资源并统一异常面。"""
        try:
            return self.mcp_runtime.list_resources(query=query, server_name=server_name, limit=limit)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_render_resource_index(self, *, query: str | None, server_name: str | None, limit: int) -> str:
        """渲染 MCP 资源索引并统一异常面。"""
        try:
            return self.mcp_runtime.render_resource_index(query=query, server_name=server_name, limit=limit)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_render_resource(self, uri: str, *, max_chars: int) -> str:
        """读取并渲染 MCP 资源。"""
        try:
            return self.mcp_runtime.render_resource(uri, max_chars=max_chars)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise GatewayNotFoundError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_search_capabilities(self, *, query: str | None, server_name: str | None, limit: int) -> tuple[object, ...]:
        """搜索 MCP 能力目录。"""
        try:
            return self.mcp_runtime.search_capabilities(query=query, server_name=server_name, limit=limit)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_render_capability_index(self, *, query: str | None, server_name: str | None, limit: int) -> str:
        """渲染 MCP 能力目录文本。"""
        try:
            return self.mcp_runtime.render_capability_index(query=query, server_name=server_name, limit=limit)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_resolve_capability(self, capability_handle: str):
        """解析能力句柄。"""
        try:
            return self.mcp_runtime.resolve_capability(capability_handle)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise GatewayNotFoundError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_resolve_tool(self, tool_name: str, *, server_name: str | None = None) -> MCPTool:
        """定位 MCP 远端工具。"""
        try:
            return self.mcp_runtime.resolve_tool(tool_name, server_name=server_name)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise GatewayNotFoundError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_call_tool(
        self,
        tool_name: str,
        *,
        arguments: JSONDict,
        server_name: str | None,
        max_chars: int,
    ):
        """调用 MCP 远端工具。"""
        try:
            return self.mcp_runtime.call_tool(
                tool_name,
                arguments=arguments,
                server_name=server_name,
                max_chars=max_chars,
            )
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise GatewayNotFoundError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_render_tool_result(self, result) -> str:
        """渲染 MCP 工具结果文本。"""
        return self.mcp_runtime.render_tool_result(result)

    @staticmethod
    def normalize_tool_failure(exc: BaseException) -> ToolExecutionResult:
        """把网关异常标准化为工具执行结果。"""
        if isinstance(exc, GatewayPermissionError):
            error_kind = 'permission_denied'
        elif isinstance(exc, (GatewayValidationError, GatewayNotFoundError, GatewayTransportError)):
            error_kind = 'tool_execution_error'
        elif isinstance(exc, GatewayRuntimeError):
            error_kind = 'tool_execution_error'
        elif isinstance(exc, (ToolPermissionError, ToolExecutionError)):
            error_kind = 'tool_execution_error'
        else:
            error_kind = 'tool_execution_error'
        return ToolExecutionResult(
            name='gateway',
            ok=False,
            content=str(exc),
            metadata={'error_kind': error_kind},
        )
