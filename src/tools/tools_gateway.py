"""tools 领域统一网关。

该文件是 tools 子系统对外唯一边界，负责：
1) 组装本地工具注册表；
2) 构建执行上下文并执行工具；
3) 把 MCP 运行时内部对象翻译为原生字典契约，避免类型泄漏。

外部仅允许通过本文件访问 tools 子系统，禁止直接引用 tools 下的其他模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Mapping

from core_contracts.errors import (
    GatewayError,
    GatewayNotFoundError,
    GatewayPermissionError,
    GatewayRuntimeError,
    GatewayTransportError,
    GatewayValidationError,
)
from core_contracts.config import ToolPermissionPolicy
from core_contracts.messaging import ToolExecutionResult
from core_contracts.primitives import JSONDict
from core_contracts.config import ExecutionPolicy, WorkspaceScope
from core_contracts.tools_contracts import ToolDescriptor, ToolExecutionContext, ToolStreamUpdate
from tools.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutor
from tools.local.filesystem_tools import build_filesystem_tools
from tools.local.shell_tools import build_shell_tool
from tools.mcp import MCPRuntime, MCPTransportError
from tools.registry import build_registry, render_openai_tools
from workspace.workspace_gateway import WorkspaceGateway


@dataclass
class ToolsGateway:
    """tools 领域唯一对外入口。

    该网关屏蔽 tools 子包内部实现细节，并把所有跨域返回值收敛为
    原生类型或 core_contracts 契约对象。网关不接收任何 domain object
    作为构造参数——所有内部依赖在实例化时自举。
    """

    _shell_security_policy: ShellSecurityPolicy = field(
        default_factory=ShellSecurityPolicy, init=False, repr=False
    )  # ShellSecurityPolicy: 内部 shell 安全策略，不对外暴露。
    _executor: ToolExecutor = field(
        default_factory=ToolExecutor, init=False, repr=False
    )  # ToolExecutor: 内部工具执行器，封装调用与错误包装。
    _mcp_runtime: MCPRuntime | None = field(
        default=None, init=False, repr=False
    )  # MCPRuntime | None: 绑定工作区后初始化的 MCP 运行时。
    _workspace_gateway: WorkspaceGateway | None = field(
        default=None, init=False, repr=False
    )  # WorkspaceGateway | None: 绑定工作区后初始化的 workspace 运行时。
    _workspace: Path | None = field(
        default=None, init=False, repr=False
    )  # Path | None: 当前绑定的工作区根目录。

    # ── 工作区绑定 ────────────────────────────────────────────────

    def bind_workspace(self, workspace: Path) -> None:
        """绑定工作区并初始化 MCP 运行时。
        Args:
            workspace (Path): 当前会话绑定的工作区根目录。
        Returns:
            None: 仅更新网关内部运行时状态。
        Raises:
            ValueError: 当传入路径不可解析时由下层抛出。
        """
        resolved = workspace.resolve()
        if self._workspace == resolved and self._mcp_runtime is not None:
            return
        self._workspace = resolved
        self._workspace_gateway = WorkspaceGateway.from_workspace(resolved)
        self._mcp_runtime = MCPRuntime.from_workspace(resolved)

    # ── 工具注册表 ─────────────────────────────────────────────────

    def default_registry(self) -> dict[str, ToolDescriptor]:
        """返回内置基础工具注册表。
        Args:
            None: 该方法不接收参数。
        Returns:
            dict[str, ToolDescriptor]: 内置工具定义映射。
        Raises:
            RuntimeError: 当工具构建过程中出现不可恢复错误时抛出。
        """
        return build_registry(
            *build_filesystem_tools(),
            build_shell_tool(self._shell_security_policy),
        )

    def _require_workspace_gateway(self) -> WorkspaceGateway:
        """获取已初始化的 workspace gateway。"""
        if self._workspace_gateway is None:
            raise GatewayRuntimeError('ToolsGateway is not bound to a workspace yet')
        return self._workspace_gateway

    def prepare_tool_registry(self, tool_registry: Mapping[str, ToolDescriptor]) -> dict[str, ToolDescriptor]:
        """应用工作区策略，生成最终可见工具注册表。"""
        return self._require_workspace_gateway().prepare_tool_registry(dict(tool_registry))

    def has_search_providers(self) -> bool:
        """当前工作区是否配置了搜索 provider。"""
        return self._require_workspace_gateway().has_search_providers()

    def search_workspace(
        self,
        query: str,
        *,
        provider_id: str | None,
        max_results: int | None,
        max_retries: int,
    ) -> JSONDict:
        """执行工作区搜索并返回统一 JSON 契约。"""
        return self._require_workspace_gateway().search(
            query,
            provider_id=provider_id,
            max_results=max_results,
            max_retries=max_retries,
        )

    def workspace_plugin_count(self) -> int:
        """返回当前工作区插件数量。"""
        return self._require_workspace_gateway().plugin_count

    def workspace_policy_count(self) -> int:
        """返回当前工作区策略数量。"""
        return self._require_workspace_gateway().policy_count

    def workspace_search_provider_count(self) -> int:
        """返回当前工作区搜索 provider 数量。"""
        return self._require_workspace_gateway().search_provider_count

    def workspace_load_error_count(self) -> int:
        """返回当前工作区加载错误数量。"""
        return self._require_workspace_gateway().load_error_count

    def mcp_server_count(self) -> int:
        """返回 MCP server 数量。"""
        return len(self._require_mcp_runtime().servers)

    def mcp_load_error_count(self) -> int:
        """返回 MCP 运行时加载错误数量。"""
        return len(self._require_mcp_runtime().load_errors)

    def extend_runtime_registry(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        *,
        delegate_agent_handler: Callable,
        workspace_search_handler: Callable,
        mcp_list_resources_handler: Callable,
        mcp_read_resource_handler: Callable,
        mcp_search_capabilities_handler: Callable,
        mcp_call_tool_handler: Callable,
    ) -> dict[str, ToolDescriptor]:
        """基于当前 runtime 状态补齐动态工具描述符。"""
        merged_registry = dict(tool_registry)

        merged_registry['delegate_agent'] = ToolDescriptor(
            name='delegate_agent',
            description='Delegate a batch of child tasks to managed sub-agents and return an aggregated summary.',
            parameters={
                'type': 'object',
                'properties': {
                    'label': {'type': 'string'},
                    'tasks': {
                        'type': 'array',
                        'minItems': 1,
                        'items': {
                            'type': 'object',
                            'properties': {
                                'task_id': {'type': 'string'},
                                'prompt': {'type': 'string'},
                                'label': {'type': 'string'},
                                'dependencies': {'type': 'array', 'items': {'type': 'string'}},
                                'resume_session_id': {'type': 'string'},
                            },
                            'required': ['prompt'],
                        },
                    },
                },
                'required': ['tasks'],
            },
            handler=delegate_agent_handler,
        )

        if self.has_search_providers():
            merged_registry['workspace_search'] = ToolDescriptor(
                name='workspace_search',
                description='Search the configured workspace search provider and return structured web results.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string'},
                        'provider_id': {'type': 'string'},
                        'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                        'max_retries': {'type': 'integer', 'minimum': 0, 'maximum': 3},
                    },
                    'required': ['query'],
                },
                handler=workspace_search_handler,
            )

        if self.has_mcp_resources() or self.has_mcp_servers():
            merged_registry.update(
                {
                    'mcp_list_resources': ToolDescriptor(
                        name='mcp_list_resources',
                        description='List MCP resources discovered from local manifests and configured MCP servers.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'query': {'type': 'string'},
                                'server_name': {'type': 'string'},
                                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                            },
                        },
                        handler=mcp_list_resources_handler,
                    ),
                    'mcp_read_resource': ToolDescriptor(
                        name='mcp_read_resource',
                        description='Read a specific MCP resource by URI.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'uri': {'type': 'string'},
                                'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 20000},
                            },
                            'required': ['uri'],
                        },
                        handler=mcp_read_resource_handler,
                    ),
                }
            )

        if self.has_mcp_servers():
            merged_registry.update(
                {
                    'mcp_search_capabilities': ToolDescriptor(
                        name='mcp_search_capabilities',
                        description='Search concise MCP capability candidates from configured MCP servers.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'query': {'type': 'string'},
                                'server_name': {'type': 'string'},
                                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                            },
                        },
                        handler=mcp_search_capabilities_handler,
                    ),
                    'mcp_call_tool': ToolDescriptor(
                        name='mcp_call_tool',
                        description='Call a remote MCP tool by capability_handle or tool_name, optionally scoped to a specific server.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'capability_handle': {'type': 'string'},
                                'tool_name': {'type': 'string'},
                                'server_name': {'type': 'string'},
                                'arguments': {'type': 'object', 'additionalProperties': True},
                                'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 20000},
                            },
                            'anyOf': [
                                {'required': ['capability_handle']},
                                {'required': ['tool_name']},
                            ],
                        },
                        handler=mcp_call_tool_handler,
                    ),
                }
            )

        return merged_registry

    def to_openai_tools(self, tool_registry: Mapping[str, ToolDescriptor]) -> list[JSONDict]:
        """把工具注册表投影为模型可见 schema 列表。
        Args:
            tool_registry (Mapping[str, ToolDescriptor]): 当前可见工具注册表。
        Returns:
            list[JSONDict]: OpenAI 兼容工具声明列表。
        Raises:
            ValueError: 当某个工具 schema 不合法时由下层抛出。
        """
        return render_openai_tools(tool_registry)

    # ── 执行上下文 ────────────────────────────────────────────────

    def build_context(
        self,
        workspace_scope: WorkspaceScope,
        execution_policy: ExecutionPolicy,
        permissions: ToolPermissionPolicy,
        *,
        tool_registry: Mapping[str, ToolDescriptor] | None = None,
        safe_env: dict[str, str] | None = None,
    ) -> ToolExecutionContext:
        """根据运行时配置构造工具执行上下文。
        Args:
            workspace_scope (WorkspaceScope): 工作区路径与运行目录约束。
            execution_policy (ExecutionPolicy): 执行超时与输出预算配置。
            permissions (ToolPermissionPolicy): 工具权限策略。
            tool_registry (Mapping[str, ToolDescriptor] | None): 可选工具注册表。
            safe_env (dict[str, str] | None): 可选安全环境变量覆盖。
        Returns:
            ToolExecutionContext: 供工具执行器消费的上下文对象。
        Raises:
            ValueError: 当上下文参数非法时由下层抛出。
        """
        return self._executor.build_context(
            workspace_scope,
            execution_policy,
            permissions,
            tool_registry=tool_registry,
            safe_env=safe_env,
        )

    # ── 普通执行 ──────────────────────────────────────────────────

    def execute(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """执行一次工具调用。
        Args:
            tool_registry (Mapping[str, ToolDescriptor]): 当前可见工具注册表。
            name (str): 目标工具名。
            arguments (JSONDict): 工具参数对象。
            context (ToolExecutionContext): 工具执行上下文。
        Returns:
            ToolExecutionResult: 标准化的执行结果。
        Raises:
            RuntimeError: 当执行器出现未捕获错误时抛出。
        """
        return self._executor.execute(tool_registry, name, arguments, context)

    # ── 流式执行 ──────────────────────────────────────────────────

    def execute_streaming(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """执行一次流式工具调用。
        Args:
            tool_registry (Mapping[str, ToolDescriptor]): 当前可见工具注册表。
            name (str): 目标工具名。
            arguments (JSONDict): 工具参数对象。
            context (ToolExecutionContext): 工具执行上下文。
        Returns:
            Iterator[ToolStreamUpdate]: 逐步产出的流式更新序列。
        Raises:
            RuntimeError: 当流式执行器出现未捕获错误时抛出。
        """
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
        """执行一次工具调用，并在流式片段出现时回调上报。
        Args:
            tool_registry (Mapping[str, ToolDescriptor]): 当前可见工具注册表。
            name (str): 目标工具名。
            arguments (JSONDict): 工具参数对象。
            context (ToolExecutionContext): 工具执行上下文。
            on_stream_update (Callable[[ToolStreamUpdate], None] | None): 可选流式片段回调。
        Returns:
            ToolExecutionResult: 最终工具结果。
        Raises:
            RuntimeError: 当执行过程出现未捕获错误时抛出。
        """
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

    # ── MCP 运行时查询 ─────────────────────────────────────────────

    def has_mcp_servers(self) -> bool:
        """当前工作区是否存在可用 MCP 服务器。
        Args:
            None: 该方法不接收参数。
        Returns:
            bool: 若存在至少一个 MCP server 则为 True。
        Raises:
            GatewayRuntimeError: 当网关尚未绑定工作区时抛出。
        """
        return bool(self._require_mcp_runtime().servers)

    def _require_mcp_runtime(self) -> MCPRuntime:
        """获取已初始化的 MCP runtime。
        Args:
            None: 该方法不接收参数。
        Returns:
            MCPRuntime: 当前绑定工作区对应的 MCP runtime。
        Raises:
            GatewayRuntimeError: 当网关尚未绑定工作区时抛出。
        """
        if self._mcp_runtime is None:
            raise GatewayRuntimeError('ToolsGateway is not bound to a workspace yet')
        return self._mcp_runtime

    def has_mcp_resources(self) -> bool:
        """当前工作区是否存在可见 MCP 资源。
        Args:
            None: 该方法不接收参数。
        Returns:
            bool: 若存在至少一个 MCP 资源则为 True。
        Raises:
            GatewayRuntimeError: 当网关尚未绑定工作区时抛出。
        """
        return bool(self._require_mcp_runtime().resources)

    # ── MCP 资源操作 ───────────────────────────────────────────────

    def mcp_list_resources(self, *, query: str | None, server_name: str | None, limit: int) -> tuple[JSONDict, ...]:
        """列出 MCP 资源并统一异常面。
        Args:
            query (str | None): 可选资源查询词。
            server_name (str | None): 可选 server 过滤条件。
            limit (int): 最大返回条目数。
        Returns:
            tuple[JSONDict, ...]: 资源契约字典序列。
        Raises:
            GatewayValidationError: 当输入参数不合法时抛出。
            GatewayTransportError: 当 MCP 传输请求失败时抛出。
        """
        if self._mcp_runtime is None:
            raise GatewayRuntimeError('ToolsGateway is not bound to a workspace yet')
        try:
            resources = self._mcp_runtime.list_resources(query=query, server_name=server_name, limit=limit)
            return tuple(resource.to_dict() for resource in resources)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_render_resource_index(self, *, query: str | None, server_name: str | None, limit: int) -> str:
        """渲染 MCP 资源索引并统一异常面。
        Args:
            query (str | None): 可选资源查询词。
            server_name (str | None): 可选 server 过滤条件。
            limit (int): 最大渲染条目数。
        Returns:
            str: 面向模型消费的索引文本。
        Raises:
            GatewayValidationError: 当输入参数不合法时抛出。
            GatewayTransportError: 当 MCP 传输请求失败时抛出。
        """
        try:
            return self._require_mcp_runtime().render_resource_index(query=query, server_name=server_name, limit=limit)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_render_resource(self, uri: str, *, max_chars: int) -> str:
        """读取并渲染 MCP 资源。
        Args:
            uri (str): 资源 URI。
            max_chars (int): 返回文本上限。
        Returns:
            str: 资源渲染后的文本内容。
        Raises:
            GatewayValidationError: 当参数不合法时抛出。
            GatewayNotFoundError: 当目标资源不存在时抛出。
            GatewayTransportError: 当 MCP 传输请求失败时抛出。
        """
        try:
            return self._require_mcp_runtime().render_resource(uri, max_chars=max_chars)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise GatewayNotFoundError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    # ── MCP 能力目录 ───────────────────────────────────────────────

    def mcp_search_capabilities(self, *, query: str | None, server_name: str | None, limit: int) -> tuple[JSONDict, ...]:
        """搜索 MCP 能力目录。
        Args:
            query (str | None): 可选能力查询词。
            server_name (str | None): 可选 server 过滤条件。
            limit (int): 最大返回条目数。
        Returns:
            tuple[JSONDict, ...]: 能力契约字典序列。
        Raises:
            GatewayValidationError: 当输入参数不合法时抛出。
            GatewayTransportError: 当 MCP 传输请求失败时抛出。
        """
        try:
            capabilities = self._require_mcp_runtime().search_capabilities(
                query=query,
                server_name=server_name,
                limit=limit,
            )
            return tuple(capability.to_dict() for capability in capabilities)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_render_capability_index(self, *, query: str | None, server_name: str | None, limit: int) -> str:
        """渲染 MCP 能力目录文本。
        Args:
            query (str | None): 可选能力查询词。
            server_name (str | None): 可选 server 过滤条件。
            limit (int): 最大渲染条目数。
        Returns:
            str: 面向模型消费的能力目录文本。
        Raises:
            GatewayValidationError: 当输入参数不合法时抛出。
            GatewayTransportError: 当 MCP 传输请求失败时抛出。
        """
        try:
            return self._require_mcp_runtime().render_capability_index(query=query, server_name=server_name, limit=limit)
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_resolve_capability(self, capability_handle: str) -> JSONDict:
        """解析能力句柄。
        Args:
            capability_handle (str): 目标能力句柄。
        Returns:
            JSONDict: 能力契约字典。
        Raises:
            GatewayValidationError: 当句柄非法时抛出。
            GatewayNotFoundError: 当能力不存在时抛出。
            GatewayTransportError: 当 MCP 传输请求失败时抛出。
        """
        try:
            capability = self._require_mcp_runtime().resolve_capability(capability_handle)
            return capability.to_dict()
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise GatewayNotFoundError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    # ── MCP 远端工具 ───────────────────────────────────────────────

    def mcp_resolve_tool(self, tool_name: str, *, server_name: str | None = None) -> JSONDict:
        """定位 MCP 远端工具。
        Args:
            tool_name (str): 工具名称。
            server_name (str | None): 可选 server 名称。
        Returns:
            JSONDict: 工具契约字典。
        Raises:
            GatewayValidationError: 当输入参数不合法时抛出。
            GatewayNotFoundError: 当工具不存在时抛出。
            GatewayTransportError: 当 MCP 传输请求失败时抛出。
        """
        try:
            tool = self._require_mcp_runtime().resolve_tool(tool_name, server_name=server_name)
            return tool.to_dict()
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
    ) -> JSONDict:
        """调用 MCP 远端工具。
        Args:
            tool_name (str): 目标工具名称。
            arguments (JSONDict): 工具参数对象。
            server_name (str | None): 可选 server 名称。
            max_chars (int): 返回文本上限。
        Returns:
            JSONDict: 工具调用结果契约字典。
        Raises:
            GatewayValidationError: 当输入参数不合法时抛出。
            GatewayNotFoundError: 当工具不存在时抛出。
            GatewayTransportError: 当 MCP 传输请求失败时抛出。
        """
        try:
            result = self._require_mcp_runtime().call_tool(
                tool_name,
                arguments=arguments,
                server_name=server_name,
                max_chars=max_chars,
            )
            return result.to_dict()
        except ValueError as exc:
            raise GatewayValidationError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise GatewayNotFoundError(str(exc)) from exc
        except MCPTransportError as exc:
            raise GatewayTransportError(str(exc)) from exc

    def mcp_render_tool_result(self, result: JSONDict) -> str:
        """渲染 MCP 工具结果文本。
        Args:
            result (JSONDict): `mcp_call_tool` 返回的结果契约。
        Returns:
            str: 面向模型消费的工具结果文本。
        Raises:
            GatewayValidationError: 当结果结构不合法时抛出。
        """
        tool_name = result.get('tool_name')
        server_name = result.get('server_name')
        is_error = result.get('is_error')
        content = result.get('content')
        if not isinstance(tool_name, str) or not isinstance(server_name, str):
            raise GatewayValidationError('Invalid MCP tool result payload: missing tool_name/server_name')
        if not isinstance(is_error, bool) or not isinstance(content, str):
            raise GatewayValidationError('Invalid MCP tool result payload: missing is_error/content')
        return '\n'.join(
            [
                '# MCP Tool Result',
                '',
                f'- Tool: {tool_name}',
                f'- Server: {server_name}',
                f'- is_error: {is_error}',
                '',
                content,
            ]
        )

    # ── 错误标准化 ─────────────────────────────────────────────────

    @staticmethod
    def normalize_tool_failure(exc: BaseException) -> ToolExecutionResult:
        """把网关异常标准化为工具执行结果。
        Args:
            exc (BaseException): 需要转换的异常对象。
        Returns:
            ToolExecutionResult: 统一格式的失败结果。
        Raises:
            None: 该方法始终返回结果对象。
        """
        if isinstance(exc, GatewayPermissionError):
            error_kind = 'permission_denied'
        elif isinstance(exc, (GatewayValidationError, GatewayNotFoundError, GatewayTransportError, GatewayRuntimeError)):
            error_kind = 'tool_execution_error'
        else:
            error_kind = 'tool_execution_error'
        return ToolExecutionResult(
            name='gateway',
            ok=False,
            content=str(exc),
            metadata={'error_kind': error_kind},
        )

