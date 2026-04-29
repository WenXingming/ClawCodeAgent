"""负责单次 agent run/resume 的 turn loop 编排。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, Iterable

from agent.delegation_service import DelegatedTaskSpec, DelegationService
from agent.prompt_processor import PromptProcessor
from agent.result_factory import ResultFactory
from agent.run_limits import RunLimits
from agent.run_state import AgentRunState
from context import ContextManager
from core_contracts.budget import BudgetConfig
from core_contracts.protocol import JSONDict, OneTurnResponse, ToolCall, ToolExecutionResult
from core_contracts.run_result import AgentRunResult
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.runtime_policy import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from openai_client import OpenAIClient
from session import SessionManager
from tools.executor import ToolExecutionError, ToolPermissionError, ToolStreamUpdate
from tools.registry import LocalTool
from tools.tool_gateway import ToolGateway
from tools.mcp import MCPRuntime, MCPTool, MCPTransportError
from workspace import SearchQueryError, WorkspaceGateway


_FILESYSTEM_WRITE_TOOL_NAMES = frozenset({'write_file', 'edit_file', 'create_directory', 'move_file'})
_MCP_MATERIALIZED_TOOL_LIMIT = 3


@dataclass
class TurnCoordinator:
    """负责单次 agent 调用的主循环推进。"""

    client: OpenAIClient  # 模型客户端。
    workspace_scope: WorkspaceScope  # 工作区范围配置。
    execution_policy: ExecutionPolicy  # 执行限制配置。
    context_policy: ContextPolicy  # 上下文治理配置。
    permissions: ToolPermissionPolicy  # 工具权限配置。
    budget_config: BudgetConfig  # 预算配置。
    session_paths: SessionPaths  # 会话路径配置。
    session_manager: SessionManager  # 会话管理门面。
    workspace_gateway: WorkspaceGateway  # 工作区领域门面。
    mcp_runtime: MCPRuntime  # MCP 运行时。
    context_manager: ContextManager  # 上下文治理门面。
    tool_gateway: ToolGateway = field(default_factory=ToolGateway)
    delegation_service: DelegationService = field(default_factory=DelegationService)  # DelegationService: 当前 run/resume 树共享的子代理编排器。
    current_agent_id: str | None = None  # str | None: 当前 agent 对应的受管代理标识；根调用与 child 调用均会设置。
    progress_reporter: Callable[[JSONDict], None] | None = None  # Callable[[JSONDict], None] | None: 可选的实时进度上报回调。
    prompt_processor: PromptProcessor | None = None
    result_factory: ResultFactory | None = None
    child_agent_factory: Callable[[str], Any] | None = None
    tool_registry: dict[str, LocalTool] = field(init=False)  # 可用工具集合。

    def run(
        self,
        *,
        prompt: str,
        run_state: AgentRunState,
        resumed_from_session_id: str | None,
    ) -> AgentRunResult:
        """在既有运行态上执行一次完整的 agent 调用。"""
        return self._run_managed_invocation(
            prompt=prompt,
            run_state=run_state,
            resumed_from_session_id=resumed_from_session_id,
        )

    def _run_managed_invocation(
        self,
        *,
        prompt: str,
        run_state: AgentRunState,
        resumed_from_session_id: str | None,
    ) -> AgentRunResult:
        """在受管代理上下文中执行一次 run 或 resume。

        根调用会在进入前重置 `DelegationService`，并为当前 run 建立 root record；
        child 调用则复用父级传入的 manager 与 current_agent_id。

        Args:
            prompt (str): 当前调用的用户输入。
            run_state (AgentRunState): 当前调用共享的动态运行态对象。
            resumed_from_session_id (str | None): 若为 resume，则记录来源 session_id。
        Returns:
            AgentRunResult: 当前 run/resume 的最终结果。
        Raises:
            Exception: 未被主循环吸收的异常会继续向上传播。
        """
        is_root_invocation = self.current_agent_id is None
        if is_root_invocation:
            self.delegation_service = DelegationService()
            self.current_agent_id = self.delegation_service.start_agent(
                prompt=prompt,
                label='root',
                resumed_from_session_id=resumed_from_session_id,
            )

        managed_agent_id = self.current_agent_id
        try:
            local_result = self._prepare_prompt(
                prompt=prompt,
                run_state=run_state,
            )
            if local_result is not None:
                result = local_result
            else:
                result = self._execute_loop(
                    run_state=run_state,
                )
        except Exception:
            if managed_agent_id is not None:
                self.delegation_service.finish_agent(
                    managed_agent_id,
                    session_id=None,
                    session_path=None,
                    turns=run_state.turns_total,
                    tool_calls=run_state.tool_call_count,
                    stop_reason='exception',
                )
            if is_root_invocation:
                self.current_agent_id = None
            raise

        if managed_agent_id is not None:
            self.delegation_service.finish_agent(
                managed_agent_id,
                session_id=result.session_id,
                session_path=result.session_path,
                turns=result.turns,
                tool_calls=result.tool_calls,
                stop_reason=result.stop_reason,
            )
        if is_root_invocation:
            self.current_agent_id = None
        return result

    def __post_init__(self) -> None:
        """初始化派生运行时对象与动态工具注册表。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            None: 该方法原地补全实例的运行时依赖。
        """
        self.tool_registry = self.tool_gateway.default_registry()
        self.tool_registry = self._register_workspace_runtime_tools(self.tool_registry)
        self.tool_registry = self.workspace_gateway.prepare_tool_registry(self.tool_registry)

    def tool_registry_view(self) -> dict[str, LocalTool]:
        """返回当前基础工具注册表的浅拷贝。"""
        return dict(self.tool_registry)

    def _require_prompt_processor(self) -> PromptProcessor:
        """返回当前绑定的 prompt processor。"""
        if self.prompt_processor is None:
            raise RuntimeError('TurnCoordinator requires a PromptProcessor')
        return self.prompt_processor

    def _require_result_factory(self) -> ResultFactory:
        """返回当前绑定的结果构造器。"""
        if self.result_factory is None:
            raise RuntimeError('TurnCoordinator requires a ResultFactory')
        return self.result_factory

    def _require_child_agent_factory(self) -> Callable[[str], Any]:
        """返回 child agent 构造器。"""
        if self.child_agent_factory is None:
            raise RuntimeError('TurnCoordinator requires a child agent factory')
        return self.child_agent_factory

    def _emit_progress_event(self, event: JSONDict) -> None:
        """向可选 reporter 发送一个实时事件。"""
        if self.progress_reporter is None:
            return
        self.progress_reporter(dict(event))

    def _record_event(self, run_state: AgentRunState, event: JSONDict) -> None:
        """把事件写入持久化列表，并同步推送到实时 reporter。"""
        stored_event = dict(event)
        run_state.events.append(stored_event)
        self._emit_progress_event(stored_event)

    def _extend_recorded_events(
        self,
        run_state: AgentRunState,
        new_events: Iterable[JSONDict],
    ) -> None:
        """批量写入并推送事件。"""
        for event in new_events:
            self._record_event(run_state, event)

    def _register_workspace_runtime_tools(
        self,
        tool_registry: dict[str, LocalTool],
    ) -> dict[str, LocalTool]:
        """把工作区相关的动态工具注册到当前工具表中。

        Args:
            tool_registry (dict[str, LocalTool]): 当前已有的工具注册表。
        Returns:
            dict[str, LocalTool]: 追加 workspace_search、MCP 与 delegate_agent 等动态工具后的注册表副本。
        """
        merged_registry = dict(tool_registry)

        merged_registry['delegate_agent'] = LocalTool(
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
            handler=self._run_delegate_agent,
        )

        if self.workspace_gateway.has_search_providers():
            merged_registry['workspace_search'] = LocalTool(
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
                handler=self._run_workspace_search,
            )

        if self.mcp_runtime.resources or self.mcp_runtime.servers:
            merged_registry.update(
                {
                    'mcp_list_resources': LocalTool(
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
                        handler=self._run_mcp_list_resources,
                    ),
                    'mcp_read_resource': LocalTool(
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
                        handler=self._run_mcp_read_resource,
                    ),
                }
            )

        # 仅向模型暴露精简的 MCP 能力搜索与统一调用门面，避免把整份工具目录直接转成上下文噪音。
        if self.mcp_runtime.servers:
            merged_registry.update(
                {
                    'mcp_search_capabilities': LocalTool(
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
                        handler=self._run_mcp_search_capabilities,
                    ),
                    'mcp_call_tool': LocalTool(
                        name='mcp_call_tool',
                        description='Call a remote MCP tool by capability_handle or tool_name, optionally scoped to a specific server.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'capability_handle': {'type': 'string'},
                                'tool_name': {'type': 'string'},
                                'server_name': {'type': 'string'},
                                'arguments': {
                                    'type': 'object',
                                    'additionalProperties': True,
                                },
                                'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 20000},
                            },
                            'anyOf': [
                                {'required': ['capability_handle']},
                                {'required': ['tool_name']},
                            ],
                        },
                        handler=self._run_mcp_call_tool,
                    ),
                }
            )

        return merged_registry

    def _run_workspace_search(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """执行 workspace_search 工具并渲染搜索结果。

        Args:
            arguments (JSONDict): workspace_search 的工具调用参数。
            context (Any): 当前工具执行上下文。
        Returns:
            str | tuple[str, JSONDict]: 渲染后的搜索结果文本，以及附带统计信息的元数据。
        Raises:
            ToolExecutionError: 当查询参数非法或底层搜索运行时调用失败时抛出。
        """
        query = self._require_tool_string(arguments, 'query')
        provider_id = self._optional_tool_string(arguments, 'provider_id')
        max_results = self._optional_tool_int(arguments, 'max_results', min_value=1, max_value=20)
        max_retries = self._optional_tool_int(arguments, 'max_retries', min_value=0, max_value=3) or 0

        try:
            response = self.workspace_gateway.search(
                query,
                provider_id=provider_id,
                max_results=max_results,
                max_retries=max_retries,
            )
        except (SearchQueryError, ValueError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        lines = [
            '# Search Results',
            '',
            f'- Provider: {response.provider.provider_id}',
            f'- Query: {response.query}',
            f'- Attempts: {response.attempts}',
            '',
        ]
        if not response.results:
            lines.append('No results returned.')
        else:
            for item in response.results:
                lines.extend(
                    [
                        f'{item.rank}. {item.title}',
                        f'URL: {item.url}',
                        f'Snippet: {item.snippet}',
                        '',
                    ]
                )

        content = self._truncate_tool_output('\n'.join(lines).rstrip(), context.max_output_chars)
        return (
            content,
            {
                'provider_id': response.provider.provider_id,
                'attempts': response.attempts,
                'result_count': len(response.results),
            },
        )

    def _run_mcp_list_resources(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """列出当前 MCP 运行时可见的资源索引。

        Args:
            arguments (JSONDict): mcp_list_resources 的工具调用参数。
            context (Any): 当前工具执行上下文。
        Returns:
            str | tuple[str, JSONDict]: 渲染后的资源索引文本，以及附带统计信息的元数据。
        Raises:
            ToolExecutionError: 当参数非法或 MCP 运行时调用失败时抛出。
        """
        query = self._optional_tool_string(arguments, 'query')
        server_name = self._optional_tool_string(arguments, 'server_name')
        limit = self._optional_tool_int(arguments, 'limit', min_value=1, max_value=100) or 20

        try:
            resources = self.mcp_runtime.list_resources(query=query, server_name=server_name, limit=limit)
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        content = self._truncate_tool_output(
            self.mcp_runtime.render_resource_index(query=query, server_name=server_name, limit=limit),
            context.max_output_chars,
        )
        return content, {'resource_count': len(resources), 'server_name': server_name or ''}

    def _run_mcp_read_resource(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """读取单个 MCP 资源并返回渲染结果。

        Args:
            arguments (JSONDict): mcp_read_resource 的工具调用参数。
            context (Any): 当前工具执行上下文。
        Returns:
            str | tuple[str, JSONDict]: 渲染后的资源内容，以及资源 URI 元数据。
        Raises:
            ToolExecutionError: 当参数非法或 MCP 运行时调用失败时抛出。
        """
        uri = self._require_tool_string(arguments, 'uri')
        max_chars = self._resolve_tool_output_limit(arguments, context, key='max_chars')

        try:
            content = self.mcp_runtime.render_resource(uri, max_chars=max_chars)
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        return content, {'uri': uri}

    def _run_mcp_search_capabilities(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """搜索当前 MCP 运行时可见的能力目录。

        Args:
            arguments (JSONDict): mcp_search_capabilities 的工具调用参数。
            context (Any): 当前工具执行上下文。
        Returns:
            str | tuple[str, JSONDict]: 渲染后的能力目录文本，以及附带统计信息的元数据。
        Raises:
            ToolExecutionError: 当参数非法或 MCP 运行时调用失败时抛出。
        """
        query = self._optional_tool_string(arguments, 'query')
        server_name = self._optional_tool_string(arguments, 'server_name')
        limit = self._optional_tool_int(arguments, 'limit', min_value=1, max_value=100) or 20

        try:
            capabilities = self.mcp_runtime.search_capabilities(query=query, server_name=server_name, limit=limit)
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        content = self._truncate_tool_output(
            self.mcp_runtime.render_capability_index(query=query, server_name=server_name, limit=limit),
            context.max_output_chars,
        )
        materialized_handles = [
            capability.handle
            for capability in capabilities[:_MCP_MATERIALIZED_TOOL_LIMIT]
        ]
        return (
            content,
            {
                'capability_count': len(capabilities),
                'server_name': server_name or '',
                'capabilities': [capability.to_dict() for capability in capabilities],
                'materialized_handles': materialized_handles,
            },
        )

    def _run_mcp_call_tool(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """调用单个 MCP 远程工具并返回渲染结果。

        Args:
            arguments (JSONDict): mcp_call_tool 的工具调用参数。
            context (Any): 当前工具执行上下文。
        Returns:
            str | tuple[str, JSONDict]: 渲染后的工具结果文本，以及 server/tool 维度的元数据。
        Raises:
            ToolExecutionError: 当参数非法或 MCP 运行时调用失败时抛出。
        """
        tool_name, server_name, capability_handle = self._resolve_mcp_tool_target(arguments)
        raw_tool_arguments = arguments.get('arguments', {})
        if raw_tool_arguments is None:
            raw_tool_arguments = {}
        if not isinstance(raw_tool_arguments, dict):
            raise ToolExecutionError('mcp_call_tool.arguments must be a JSON object')

        return self._execute_mcp_tool_call(
            tool_name=tool_name,
            server_name=server_name,
            capability_handle=capability_handle,
            raw_tool_arguments=raw_tool_arguments,
            context=context,
            max_chars=self._resolve_tool_output_limit(arguments, context, key='max_chars'),
        )

    def _resolve_mcp_tool_target(self, arguments: JSONDict) -> tuple[str, str | None, str | None]:
        """把 mcp_call_tool 的输入统一解析为真实工具目标。

        Args:
            arguments (JSONDict): mcp_call_tool 的原始参数对象。
        Returns:
            tuple[str, str | None, str | None]: 解析后的工具名、server 名与能力句柄。
        Raises:
            ToolExecutionError: 当 tool_name 与 capability_handle 都缺失，或显式参数与能力句柄冲突时抛出。
        """
        capability_handle = self._optional_tool_string(arguments, 'capability_handle')
        tool_name = self._optional_tool_string(arguments, 'tool_name')
        server_name = self._optional_tool_string(arguments, 'server_name')

        if capability_handle is None:
            if tool_name is None:
                raise ToolExecutionError('tool_name or capability_handle must be a non-empty string')
            return tool_name, server_name, None

        capability = self.mcp_runtime.resolve_capability(capability_handle)
        if tool_name is not None and tool_name != capability.tool_name:
            raise ToolExecutionError('tool_name does not match capability_handle')
        if server_name is not None and server_name != capability.server_name:
            raise ToolExecutionError('server_name does not match capability_handle')
        return capability.tool_name, capability.server_name, capability.handle

    def _execute_mcp_tool_call(
        self,
        *,
        tool_name: str,
        server_name: str | None,
        capability_handle: str | None,
        raw_tool_arguments: JSONDict,
        context,
        max_chars: int,
    ) -> str | tuple[str, JSONDict]:
        """执行一次远端 MCP 工具调用并统一渲染结果。"""
        try:
            remote_tool = self.mcp_runtime.resolve_tool(tool_name, server_name=server_name)
            self._ensure_mcp_tool_allowed(remote_tool, context)
            result = self.mcp_runtime.call_tool(
                tool_name,
                arguments=dict(raw_tool_arguments),
                server_name=server_name,
                max_chars=max_chars,
            )
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        content = self._truncate_tool_output(
            self.mcp_runtime.render_tool_result(result),
            context.max_output_chars,
        )
        metadata = {
            'server_name': result.server_name,
            'tool_name': result.tool_name,
            'is_error': result.is_error,
        }
        if capability_handle is not None:
            metadata['capability_handle'] = capability_handle
        return content, metadata

    def _run_materialized_mcp_tool(
        self,
        capability_handle: str,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """执行由 capability window 物化出的临时 MCP 工具。"""
        if not isinstance(arguments, dict):
            raise ToolExecutionError('materialized MCP tool arguments must be a JSON object')

        try:
            capability = self.mcp_runtime.resolve_capability(capability_handle)
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        return self._execute_mcp_tool_call(
            tool_name=capability.tool_name,
            server_name=capability.server_name,
            capability_handle=capability.handle,
            raw_tool_arguments=arguments,
            context=context,
            max_chars=context.max_output_chars,
        )

    def _build_effective_tool_registry(
        self,
        run_state: AgentRunState,
    ) -> dict[str, LocalTool]:
        """构建当前轮次对模型和执行器可见的完整工具表。"""
        effective_registry = dict(self.tool_registry)
        effective_registry.update(self._build_materialized_mcp_tool_registry(run_state))
        run_state.set_effective_tool_registry(effective_registry)
        return effective_registry

    def _build_materialized_mcp_tool_registry(
        self,
        run_state: AgentRunState,
    ) -> dict[str, LocalTool]:
        """根据当前会话的 capability window 构建临时物化工具表。"""
        materialized_tools: dict[str, LocalTool] = {}
        for capability_handle in run_state.materialized_mcp_capabilities():
            tool = self._build_materialized_mcp_tool(capability_handle)
            if tool is None:
                continue
            materialized_tools[tool.name] = tool
        return materialized_tools

    def _build_materialized_mcp_tool(self, capability_handle: str) -> LocalTool | None:
        """把单个 capability handle 物化为当前轮次可调用的临时工具。"""
        try:
            capability = self.mcp_runtime.resolve_capability(capability_handle)
            remote_tool = self.mcp_runtime.resolve_tool(
                capability.tool_name,
                server_name=capability.server_name,
            )
        except (MCPTransportError, ValueError, FileNotFoundError):
            return None

        description = capability.description or f'Materialized MCP capability from server {capability.server_name}.'
        return LocalTool(
            name=self._materialized_mcp_tool_name(capability.handle),
            description=description,
            parameters=dict(remote_tool.input_schema),
            handler=partial(self._run_materialized_mcp_tool, capability.handle),
        )

    @staticmethod
    def _materialized_mcp_tool_name(capability_handle: str) -> str:
        """把 capability handle 转成稳定的临时工具名。"""
        sanitized = [character if character.isalnum() else '_' for character in capability_handle]
        return 'mcp_cap_' + ''.join(sanitized).strip('_')

    def _update_mcp_capability_window(
        self,
        run_state: AgentRunState,
        tool_call: ToolCall,
        tool_result: ToolExecutionResult,
    ) -> None:
        """在工具执行完成后同步会话内的 capability shortlist 与物化窗口。"""
        if tool_call.name != 'mcp_search_capabilities' or not tool_result.ok:
            return

        raw_shortlist = tool_result.metadata.get('capabilities')
        raw_materialized_handles = tool_result.metadata.get('materialized_handles')
        if not isinstance(raw_shortlist, list):
            raw_shortlist = []
        if not isinstance(raw_materialized_handles, list):
            raw_materialized_handles = []
        shortlist = [
            dict(item)
            for item in raw_shortlist
            if isinstance(item, dict)
        ]
        materialized_handles = [
            item.strip()
            for item in raw_materialized_handles
            if isinstance(item, str) and item.strip()
        ]
        run_state.update_mcp_capability_window(
            shortlist=shortlist,
            materialized_handles=materialized_handles,
        )

    @staticmethod
    def _ensure_mcp_tool_allowed(remote_tool: MCPTool, context) -> None:
        """检查远端工具是否越过当前会话权限边界。"""
        if remote_tool.server_name == 'filesystem' and remote_tool.name in _FILESYSTEM_WRITE_TOOL_NAMES:
            if not context.permissions.allow_file_write:
                raise ToolPermissionError('File write permission denied: allow_file_write=false')

    def _run_delegate_agent(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """构造 delegate_agent 的注册工具返回值。

        该方法主要用于向模型暴露统一工具协议；真正的执行会在 turn loop 中
        通过 `_execute_delegate_agent_tool()` 走专门编排分支，以便保留 child/group 事件与
        delegated task budget 语义。

        Args:
            arguments (JSONDict): 工具调用参数。
            context (Any): 工具执行上下文。
        Returns:
            str | tuple[str, JSONDict]: delegate_agent 的文本摘要与结构化元数据。
        """
        result, _ = self._execute_delegate_agent_tool(arguments, context)
        return result.content, dict(result.metadata)

    def _execute_delegate_agent_tool(
        self,
        arguments: JSONDict,
        context,
    ) -> tuple[ToolExecutionResult, list[JSONDict]]:
        """执行 delegate_agent 的专用编排逻辑。

        Args:
            arguments (JSONDict): delegate_agent 的调用参数。
            context (Any): 当前工具执行上下文。
        Returns:
            tuple[ToolExecutionResult, list[JSONDict]]: 工具结果与需要写入 runtime 的事件列表。
        """
        if self.current_agent_id is None:
            return (
                ToolExecutionResult(
                    name='delegate_agent',
                    ok=False,
                    content='delegate_agent requires an active parent agent context.',
                    metadata={'error_kind': 'tool_execution_error'},
                ),
                [],
            )

        try:
            group_label = self._optional_tool_string(arguments, 'label')
            task_specs = self._parse_delegate_task_specs(arguments)
            planned_child_count = self.delegation_service.child_agent_count() + len(task_specs)
            max_delegated_tasks = self.budget_config.max_delegated_tasks
            if max_delegated_tasks is not None and planned_child_count > max_delegated_tasks:
                blocked_event = {
                    'type': 'delegate_group_blocked',
                    'parent_agent_id': self.current_agent_id,
                    'requested_children': len(task_specs),
                    'existing_children': self.delegation_service.child_agent_count(),
                    'max_delegated_tasks': max_delegated_tasks,
                }
                return (
                    ToolExecutionResult(
                        name='delegate_agent',
                        ok=False,
                        content=(
                            'delegate_agent blocked: '
                            f'requested {len(task_specs)} child tasks would exceed '
                            f'max_delegated_tasks={max_delegated_tasks}.'
                        ),
                        metadata={
                            'error_kind': 'delegated_task_limit',
                            'requested_children': len(task_specs),
                            'existing_children': self.delegation_service.child_agent_count(),
                            'max_delegated_tasks': max_delegated_tasks,
                        },
                    ),
                    [blocked_event],
                )

            group_id = self.delegation_service.start_group(
                label=group_label,
                parent_agent_id=self.current_agent_id,
                strategy='serial',
            )
            batches = self.delegation_service.plan_batches(task_specs)
            delegate_events: list[JSONDict] = [
                {
                    'type': 'delegate_group_start',
                    'group_id': group_id,
                    'label': group_label,
                    'parent_agent_id': self.current_agent_id,
                    'child_count': len(task_specs),
                    'batch_count': len(batches),
                }
            ]

            child_agent_ids: dict[str, str] = {}
            child_indices: dict[str, int] = {}
            for child_index, task in enumerate(task_specs):
                child_indices[task.task_id] = child_index
                child_agent_ids[task.task_id] = self.delegation_service.start_agent(
                    prompt=task.prompt,
                    parent_agent_id=self.current_agent_id,
                    group_id=group_id,
                    child_index=child_index,
                    label=task.label,
                    task_id=task.task_id,
                    resumed_from_session_id=task.resume_session_id,
                )

            completed_children = 0
            failed_children = 0
            dependency_skips = 0
            child_outcomes: dict[str, dict[str, object]] = {}
            max_batch_size = max((len(batch) for batch in batches), default=0)

            for batch_index, batch in enumerate(batches, start=1):
                for task in batch:
                    child_index = child_indices[task.task_id]
                    child_agent_id = child_agent_ids[task.task_id]
                    failed_dependencies = [
                        dependency_id
                        for dependency_id in task.dependencies
                        if not bool(child_outcomes.get(dependency_id, {}).get('ok', False))
                    ]
                    if failed_dependencies:
                        self.delegation_service.skip_agent(child_agent_id, reason='dependency_skipped')
                        dependency_skips += 1
                        child_outcomes[task.task_id] = {
                            'ok': False,
                            'stop_reason': 'dependency_skipped',
                            'agent_id': child_agent_id,
                        }
                        delegate_events.append(
                            {
                                'type': 'delegate_child_skipped',
                                'group_id': group_id,
                                'agent_id': child_agent_id,
                                'task_id': task.task_id,
                                'child_index': child_index,
                                'dependencies': list(task.dependencies),
                                'failed_dependencies': failed_dependencies,
                                'reason': 'dependency_skipped',
                            }
                        )
                        continue

                    delegate_events.append(
                        {
                            'type': 'delegate_child_start',
                            'group_id': group_id,
                            'agent_id': child_agent_id,
                            'task_id': task.task_id,
                            'child_index': child_index,
                            'batch_index': batch_index,
                            'resumed_from_session_id': task.resume_session_id,
                        }
                    )

                    child_agent = self._require_child_agent_factory()(child_agent_id)
                    if task.resume_session_id:
                        session_snapshot = self.session_manager.load_session(task.resume_session_id)
                        child_result = child_agent.resume(task.prompt, session_snapshot)
                    else:
                        child_result = child_agent.run(task.prompt)

                    child_ok = self._is_successful_delegate_stop_reason(child_result.stop_reason)
                    if child_ok:
                        completed_children += 1
                    else:
                        failed_children += 1

                    child_outcomes[task.task_id] = {
                        'ok': child_ok,
                        'stop_reason': child_result.stop_reason or 'completed',
                        'agent_id': child_agent_id,
                    }
                    delegate_events.append(
                        {
                            'type': 'delegate_child_complete',
                            'group_id': group_id,
                            'agent_id': child_agent_id,
                            'task_id': task.task_id,
                            'child_index': child_index,
                            'batch_index': batch_index,
                            'ok': child_ok,
                            'stop_reason': child_result.stop_reason,
                            'session_id': child_result.session_id,
                            'session_path': child_result.session_path,
                            'turns': child_result.turns,
                            'tool_calls': child_result.tool_calls,
                        }
                    )

            group_status = 'completed' if failed_children == 0 and dependency_skips == 0 else 'completed_with_failures'
            self.delegation_service.finish_group(
                group_id,
                status=group_status,
                completed_children=completed_children,
                failed_children=failed_children,
                batch_count=len(batches),
                max_batch_size=max_batch_size,
                dependency_skips=dependency_skips,
            )
            group_summary = self.delegation_service.group_summary(group_id) or {}
            delegate_events.append(
                {
                    'type': 'delegate_group_complete',
                    'group_id': group_id,
                    'status': group_status,
                    'summary': dict(group_summary),
                }
            )

            child_records = [
                self._managed_agent_record_to_metadata(record)
                for record in self.delegation_service.group_children(group_id)
            ]
            return (
                ToolExecutionResult(
                    name='delegate_agent',
                    ok=True,
                    content=self._truncate_tool_output(
                        self._render_delegate_summary(group_summary, child_records),
                        context.max_output_chars,
                    ),
                    metadata={
                        'group_id': group_id,
                        'group_summary': dict(group_summary),
                        'delegate_children': child_records,
                        'lineage': {
                            'parent_agent_id': self.current_agent_id,
                            'group_id': group_id,
                            'child_agent_ids': [record['agent_id'] for record in child_records],
                        },
                    },
                ),
                delegate_events,
            )
        except (ToolExecutionError, ValueError) as exc:
            return (
                ToolExecutionResult(
                    name='delegate_agent',
                    ok=False,
                    content=str(exc),
                    metadata={'error_kind': 'tool_execution_error'},
                ),
                [],
            )

    def _parse_delegate_task_specs(self, arguments: JSONDict) -> tuple[DelegatedTaskSpec, ...]:
        """解析 delegate_agent 的 tasks 参数。

        Args:
            arguments (JSONDict): delegate_agent 原始参数对象。
        Returns:
            tuple[DelegatedTaskSpec, ...]: 标准化后的子任务规格列表。
        Raises:
            ToolExecutionError: 当 tasks 字段缺失、为空或元素非法时抛出。
        """
        raw_tasks = arguments.get('tasks')
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ToolExecutionError('tasks must be a non-empty array')

        task_specs: list[DelegatedTaskSpec] = []
        for index, item in enumerate(raw_tasks, start=1):
            if not isinstance(item, dict):
                raise ToolExecutionError('tasks must contain JSON objects')
            payload = dict(item)
            if 'task_id' not in payload and 'taskId' not in payload:
                payload['task_id'] = f'task-{index:03d}'
            try:
                task_specs.append(DelegatedTaskSpec.from_dict(payload))
            except ValueError as exc:
                raise ToolExecutionError(str(exc)) from exc
        return tuple(task_specs)

    @staticmethod
    def _is_successful_delegate_stop_reason(stop_reason: str | None) -> bool:
        """判断 child agent 的 stop_reason 是否可视为成功完成。

        Args:
            stop_reason (str | None): child agent 最终 stop_reason。
        Returns:
            bool: 若可视为成功收敛则返回 True，否则返回 False。
        """
        if stop_reason is None:
            return True
        if stop_reason.endswith('_error'):
            return False
        return stop_reason not in {
            'max_turns',
            'token_limit',
            'cost_limit',
            'tool_call_limit',
            'model_call_limit',
            'session_turns_limit',
            'delegated_task_limit',
            'dependency_skipped',
        }

    @staticmethod
    def _managed_agent_record_to_metadata(record) -> JSONDict:
        """把 DelegationService child record 转为工具元数据友好的字典。

        Args:
            record (Any): DelegationService 返回的 child record。
        Returns:
            JSONDict: 适合写入 tool metadata 的轻量字典。
        """
        return {
            'agent_id': record.agent_id,
            'task_id': record.task_id,
            'child_index': record.child_index,
            'label': record.label,
            'status': record.status.value,
            'stop_reason': record.stop_reason,
            'session_id': record.session_id,
            'session_path': record.session_path,
            'turns': record.turns,
            'tool_calls': record.tool_calls,
            'resumed_from_session_id': record.resumed_from_session_id,
        }

    @staticmethod
    def _render_delegate_summary(group_summary: JSONDict, child_records: list[JSONDict]) -> str:
        """把 delegate_agent 结果渲染为可读文本摘要。

        Args:
            group_summary (JSONDict): group 聚合摘要。
            child_records (list[JSONDict]): child 记录列表。
        Returns:
            str: 面向模型和用户的可读文本摘要。
        """
        lines = [
            '# Delegation Summary',
            '',
            f"- Group id: {group_summary.get('group_id', 'unknown')}",
            f"- Parent agent id: {group_summary.get('parent_agent_id', 'unknown')}",
            f"- Status: {group_summary.get('status', 'unknown')}",
            f"- Child count: {group_summary.get('child_count', 0)}",
            f"- Completed children: {group_summary.get('completed_children', 0)}",
            f"- Failed children: {group_summary.get('failed_children', 0)}",
            f"- Dependency skips: {group_summary.get('dependency_skips', 0)}",
            f"- Batch count: {group_summary.get('batch_count', 0)}",
            f"- Max batch size: {group_summary.get('max_batch_size', 0)}",
            '',
            '## Child Results',
            '',
        ]
        stop_reason_counts = group_summary.get('stop_reason_counts', {})
        if isinstance(stop_reason_counts, dict) and stop_reason_counts:
            lines.append('- Stop reasons: ' + ', '.join(f'{key}={value}' for key, value in sorted(stop_reason_counts.items())))
            lines.append('')

        if not child_records:
            lines.append('No child agents were executed.')
            return '\n'.join(lines)

        for record in child_records:
            lines.extend(
                [
                    f"{record.get('child_index', '?')}. {record.get('task_id') or record.get('agent_id')}",
                    f"Status: {record.get('status', 'unknown')}",
                    f"Stop reason: {record.get('stop_reason', 'n/a')}",
                    f"Session: {record.get('session_id', 'n/a')}",
                    '',
                ]
            )
        return '\n'.join(lines).rstrip()

    @staticmethod
    def _truncate_tool_output(content: str, max_chars: int) -> str:
        """按给定上限截断工具输出文本。

        Args:
            content (str): 原始工具输出文本。
            max_chars (int): 允许保留的最大字符数。
        Returns:
            str: 超长时带有截断提示的输出文本；否则返回原文。
        """
        if len(content) <= max_chars:
            return content
        omitted = len(content) - max_chars
        suffix = f'\n\n... truncated {omitted} characters'
        keep = max(0, max_chars - len(suffix))
        return content[:keep] + suffix

    @staticmethod
    def _require_tool_string(arguments: JSONDict, key: str) -> str:
        """读取必填字符串型工具参数。

        Args:
            arguments (JSONDict): 工具参数字典。
            key (str): 目标字段名。
        Returns:
            str: 去除首尾空白后的参数值。
        Raises:
            ToolExecutionError: 当字段缺失、为空或不是字符串时抛出。
        """
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ToolExecutionError(f'{key} must be a non-empty string')
        return value.strip()

    @staticmethod
    def _optional_tool_string(arguments: JSONDict, key: str) -> str | None:
        """读取可选字符串型工具参数。

        Args:
            arguments (JSONDict): 工具参数字典。
            key (str): 目标字段名。
        Returns:
            str | None: 去除首尾空白后的参数值；未提供或为空时返回 None。
        Raises:
            ToolExecutionError: 当字段存在但不是字符串时抛出。
        """
        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ToolExecutionError(f'{key} must be a string when provided')
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _optional_tool_int(
        arguments: JSONDict,
        key: str,
        *,
        min_value: int,
        max_value: int,
    ) -> int | None:
        """读取可选整型工具参数，并校验范围。

        Args:
            arguments (JSONDict): 工具参数字典。
            key (str): 目标字段名。
            min_value (int): 允许的最小值。
            max_value (int): 允许的最大值。
        Returns:
            int | None: 参数存在时返回整数值；未提供时返回 None。
        Raises:
            ToolExecutionError: 当字段存在但不是整数或超出范围时抛出。
        """
        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolExecutionError(f'{key} must be an integer when provided')
        if value < min_value or value > max_value:
            raise ToolExecutionError(f'{key} must be between {min_value} and {max_value}')
        return value

    def _resolve_tool_output_limit(
        self,
        arguments: JSONDict,
        context,
        *,
        key: str,
    ) -> int:
        """解析单次工具调用允许使用的输出上限。

        Args:
            arguments (JSONDict): 工具参数字典。
            context (Any): 当前工具执行上下文。
            key (str): 表示输出上限字段名的参数键。
        Returns:
            int: 工具请求值与上下文上限中的较小值。
        """
        requested = self._optional_tool_int(arguments, key, min_value=1, max_value=20000)
        if requested is None:
            return context.max_output_chars
        return min(requested, context.max_output_chars)

    def _prepare_prompt(
        self,
        *,
        prompt: str,
        run_state: AgentRunState,
    ) -> AgentRunResult | None:
        """在 prompt 写入 session_state 前执行 prompt 分流。"""
        return self._require_prompt_processor().prepare(prompt=prompt, run_state=run_state)

    def _execute_loop(
        self,
        *,
        run_state: AgentRunState,
    ) -> AgentRunResult:
        """run / resume 共用的 turn loop。

        usage_delta  只统计本次执行的增量。
        usage_total  = usage_baseline + usage_delta。
        cost         = cost_baseline + estimate_cost_usd(usage_delta)。

        Returns:
            AgentRunResult: 达到停止条件后的最终运行结果。
        """
        guard = RunLimits(
            budget=self.budget_config,
            pricing=self.client.model_config.pricing,
            cost_baseline=run_state.cost_baseline,
        )
        for turn_index in range(1, self.execution_policy.max_turns + 1):
            run_state.begin_turn(turn_index)

            effective_tool_registry = self._build_effective_tool_registry(run_state)
            tool_context = self.tool_gateway.build_context(
                self.workspace_scope,
                self.execution_policy,
                self.permissions,
                tool_registry=effective_tool_registry,
                safe_env=self.workspace_gateway.safe_env,
            )
            openai_tools = self.tool_gateway.to_openai_tools(effective_tool_registry)
            pre_model_outcome = self.context_manager.run_pre_model_cycle(
                run_state=run_state,
                budget_config=self.budget_config,
                context_policy=self.context_policy,
                guard=guard,
                openai_tools=openai_tools,
            )
            pre_model_stop = pre_model_outcome.pre_model_stop
            self._extend_recorded_events(run_state, pre_model_outcome.events)

            # 模型调用前四维预算检查（session_turns / model_calls / token / cost）
            if pre_model_stop is not None:
                return self._early_stop(run_state=run_state, stop_reason=pre_model_stop)

            self._emit_progress_event({'type': 'model_start', 'turn': turn_index})
            response, reactive_stop = self._complete_with_reactive_compact(
                run_state=run_state,
                openai_tools=openai_tools,
                guard=guard,
            )
            if reactive_stop is not None:
                return self._early_stop(run_state=run_state, stop_reason=reactive_stop)
            if response is None:
                run_state.stop_reason = 'backend_error'
                return self._build_run_result(run_state=run_state)

            run_state.session_state.append_assistant_turn(response)
            if response.content:
                run_state.final_output = response.content

            self._record_event(
                run_state,
                {
                    'type': 'model_turn',
                    'turn': turn_index,
                    'finish_reason': response.finish_reason,
                    'tool_calls': len(response.tool_calls),
                },
            )

            # 没有工具调用时，说明当前任务已收敛
            if not response.tool_calls:
                run_state.stop_reason = response.finish_reason or 'completed'
                return self._build_run_result(run_state=run_state)

            # 执行工具调用并回填结果
            for tool_call in response.tool_calls:
                before_hooks = self.workspace_gateway.get_before_hooks(tool_call.name)
                after_hooks = self.workspace_gateway.get_after_hooks(tool_call.name)

                for hook in before_hooks:
                    self._append_tool_hook_message(
                        run_state,
                        hook=hook,
                        tool_call=tool_call,
                    )

                metadata_updates: JSONDict = {
                    'preflight_sources': [hook['source'] for hook in before_hooks],
                    'after_hook_sources': [hook['source'] for hook in after_hooks],
                }

                self._emit_progress_event(
                    {
                        'type': 'tool_start',
                        'turn': turn_index,
                        'tool_call_id': tool_call.id,
                        'tool_name': tool_call.name,
                    }
                )

                block_decision = self.workspace_gateway.resolve_block(tool_call.name)

                if block_decision is not None:
                    tool_result = self._make_blocked_tool_result(
                        tool_call,
                        block_decision,
                        metadata_updates,
                    )
                    self._record_event(
                        run_state,
                        {
                            'type': 'tool_blocked',
                            'turn': turn_index,
                            'tool_call_id': tool_call.id,
                            'tool_name': tool_call.name,
                            'source': block_decision['source'],
                            'source_name': block_decision['source_name'],
                            'reason': block_decision['reason'],
                        },
                    )
                else:
                    if tool_call.name == 'delegate_agent':
                        tool_result, delegate_events = self._execute_delegate_agent_tool(
                            tool_call.arguments,
                            tool_context,
                        )
                        self._extend_recorded_events(run_state, delegate_events)
                    else:
                        tool_result = self._execute_tool_call(
                            run_state=run_state,
                            tool_call=tool_call,
                            tool_context=tool_context,
                        )
                    tool_result = self._merge_tool_result_metadata(tool_result, metadata_updates)

                self._update_mcp_capability_window(run_state, tool_call, tool_result)
                run_state.record_tool_result(tool_call, tool_result)

                for hook in after_hooks:
                    self._append_tool_hook_message(
                        run_state,
                        hook=hook,
                        tool_call=tool_call,
                    )

                self._record_event(
                    run_state,
                    {
                        'type': 'tool_result',
                        'turn': turn_index,
                        'tool_call_id': tool_call.id,
                        'tool_name': tool_call.name,
                        'ok': tool_result.ok,
                        'error_kind': tool_result.metadata.get('error_kind'),
                        'metadata': dict(tool_result.metadata),
                    },
                )

                if tool_result.metadata.get('error_kind') == 'delegated_task_limit':
                    return self._early_stop(run_state=run_state, stop_reason='delegated_task_limit')

                # 工具执行后预算检查
                if stop := guard.check_post_tool(run_state.tool_call_count):
                    return self._early_stop(run_state=run_state, stop_reason=stop)

        # 达到最大轮数限制，返回结果
        run_state.stop_reason = 'max_turns'
        return self._build_run_result(run_state=run_state)

    def _early_stop(
        self,
        *,
        run_state: AgentRunState,
        stop_reason: str,
    ) -> AgentRunResult:
        """预算闸门触发时的统一提前退出路径。

        统一追加 budget_stop 事件并调用 _build_run_result，
        消除六处重复的事件追加 + 结果构建模式。
        """
        run_state.stop_reason = stop_reason
        self._record_event(run_state, {'type': 'budget_stop', 'reason': stop_reason, 'turn': run_state.turn_index})
        return self._build_run_result(run_state=run_state)

    def _complete_with_reactive_compact(
        self,
        *,
        run_state: AgentRunState,
        openai_tools: list[JSONDict],
        guard: RunLimits,
    ) -> tuple[OneTurnResponse | None, str | None]:
        """执行一次模型调用；必要时在 context-length 错误后进行 reactive compact 重试。"""
        reactive_outcome = self.context_manager.complete_with_reactive_compact(
            run_state=run_state,
            budget_config=self.budget_config,
            context_policy=self.context_policy,
            openai_tools=openai_tools,
            guard=guard,
        )
        self._extend_recorded_events(run_state, reactive_outcome.events)
        return reactive_outcome.response, reactive_outcome.stop_reason

    def _execute_tool_call(
        self,
        *,
        run_state: AgentRunState,
        tool_call: ToolCall,
        tool_context,
    ) -> ToolExecutionResult:
        """执行单个工具调用，必要时发射实时 stdout 或 stderr 事件。"""
        def _emit_stream_update(update: ToolStreamUpdate) -> None:
            if self.progress_reporter is None or not update.chunk:
                return
            self._emit_progress_event(
                {
                    'type': 'tool_stream',
                    'turn': run_state.turn_index,
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'stream': update.kind,
                    'chunk': update.chunk,
                    'metadata': dict(update.metadata),
                }
            )

        return self.tool_gateway.execute_call(
            run_state.effective_tool_registry,
            tool_call.name,
            tool_call.arguments,
            tool_context,
            on_stream_update=_emit_stream_update,
        )

    @staticmethod
    def _merge_tool_result_metadata(
        result: ToolExecutionResult,
        metadata_updates: JSONDict,
    ) -> ToolExecutionResult:
        """把附加元数据合并到工具结果中。

        Args:
            result (ToolExecutionResult): 原始工具执行结果。
            metadata_updates (JSONDict): 需要叠加到结果中的附加元数据。
        Returns:
            ToolExecutionResult: 合并元数据后的新工具结果对象。
        """
        merged_metadata = dict(result.metadata)
        for key, value in metadata_updates.items():
            if value:
                merged_metadata[key] = value
        return ToolExecutionResult(
            name=result.name,
            ok=result.ok,
            content=result.content,
            metadata=merged_metadata,
        )

    def _make_blocked_tool_result(
        self,
        tool_call: ToolCall,
        block_decision: JSONDict,
        metadata_updates: JSONDict,
    ) -> ToolExecutionResult:
        """根据阻断决策构造统一的工具阻断结果。

        Args:
            tool_call (ToolCall): 当前被阻断的工具调用对象。
            block_decision (JSONDict): 来自插件或策略运行时的阻断决策。
            metadata_updates (JSONDict): 需要叠加到结果中的附加元数据。
        Returns:
            ToolExecutionResult: 统一结构化后的阻断工具结果。
        """
        merged_metadata = dict(metadata_updates)
        merged_metadata.update(
            {
                'error_kind': 'tool_blocked',
                'blocked_by': block_decision['source'],
                'blocked_by_name': block_decision['source_name'],
                'block_reason': block_decision['reason'],
            }
        )
        return ToolExecutionResult(
            name=tool_call.name,
            ok=False,
            content=str(block_decision['message']),
            metadata=merged_metadata,
        )

    def _append_tool_hook_message(
        self,
        run_state: AgentRunState,
        *,
        hook: JSONDict,
        tool_call: ToolCall,
    ) -> None:
        """把工具 hook 消息写入会话并同步记录对应事件。

        Args:
            run_state (AgentRunState): 当前调用共享的动态运行态对象。
            hook (JSONDict): 当前要写入的 hook 定义。
            tool_call (ToolCall): 触发当前 hook 的工具调用对象。
        Returns:
            None: 该方法原地更新会话状态并追加事件。
        """
        phase = str(hook.get('phase', 'before'))
        event_type = 'tool_preflight' if phase == 'before' else 'tool_after_hook'
        metadata = {
            'phase': phase,
            'tool_call_id': tool_call.id,
            'tool_name': tool_call.name,
            'source': hook.get('source', 'unknown'),
            'source_name': hook.get('source_name', 'unknown'),
        }
        run_state.session_state.append_runtime_message(str(hook.get('content', '')), metadata=metadata)
        self._record_event(
            run_state,
            {
                'type': event_type,
                'turn': run_state.turn_index,
                'tool_call_id': tool_call.id,
                'tool_name': tool_call.name,
                'source': hook.get('source', 'unknown'),
                'source_name': hook.get('source_name', 'unknown'),
                'phase': phase,
            },
        )

    def _build_run_result(
        self,
        *,
        run_state: AgentRunState,
    ) -> AgentRunResult:
        """统一构造最终运行结果并落盘会话快照。"""
        return self._require_result_factory().build(run_state)
