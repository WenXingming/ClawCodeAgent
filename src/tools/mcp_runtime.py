"""MCP 运行时门面。

该模块把 manifest 发现、transport 请求和文本渲染三层能力收束为单一入口，
供 Agent 在一个对象上完成资源发现、资源读取、工具发现和工具调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .mcp_manifest_loader import MCPManifestLoader, MCP_SCHEMA_VERSION, normalize_name, normalize_optional_text
from .mcp_models import MCPLoadError, MCPResource, MCPServerProfile, MCPTool, MCPToolCallResult, MCPTransportError
from .mcp_renderer import MCPRenderer
from .mcp_transport import MCPTransportClient


@dataclass
class MCPRuntime:
    """统一承载 MCP 资源与工具访问能力。

    外部通常通过 from_workspace 创建实例，然后沿着两条主线使用它：
    一条是资源发现与读取，另一条是远端工具发现与调用。类内部只负责协调
    manifest loader、transport client 与 renderer，不直接承载协议实现细节。
    """

    workspace: Path  # Path: 当前运行时对应的工作区根目录。
    resources: tuple[MCPResource, ...] = ()  # tuple[MCPResource, ...]: manifest 中发现的本地资源。
    servers: tuple[MCPServerProfile, ...] = ()  # tuple[MCPServerProfile, ...]: 可连接的 MCP server 配置。
    load_errors: tuple[MCPLoadError, ...] = ()  # tuple[MCPLoadError, ...]: manifest 加载阶段采集到的错误。
    schema_version: int = MCP_SCHEMA_VERSION  # int: 当前运行时使用的 manifest schema 版本。
    _transport_client: MCPTransportClient = field(init=False, repr=False)  # MCPTransportClient: 远端请求执行器。
    _renderer: MCPRenderer = field(init=False, repr=False)  # MCPRenderer: 文本渲染与裁剪器。

    def __post_init__(self) -> None:
        """补齐运行时内部依赖并规范化工作区路径。

        Args:
            None: 无参数。
        Returns:
            None: 无返回值。
        """
        self.workspace = self.workspace.resolve()  # Path: 解析后的工作区绝对路径。
        self._transport_client = MCPTransportClient()  # MCPTransportClient: 默认 transport 调度器。
        self._renderer = MCPRenderer()  # MCPRenderer: 默认文本渲染器。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'MCPRuntime':
        """从工作区根目录创建一个已完成 manifest 发现的运行时。

        Args:
            workspace (Path): 工作区根目录。
        Returns:
            MCPRuntime: 初始化完成的运行时实例。
        """
        loader = MCPManifestLoader(workspace)
        resources, servers, load_errors = loader.load()
        return cls(
            workspace=workspace.resolve(),
            resources=resources,
            servers=servers,
            load_errors=load_errors,
        )

    def render_summary(self) -> str:
        """输出当前运行时发现结果的摘要文本。

        Args:
            None: 无参数。
        Returns:
            str: 资源数、server 数及部分 server 列表组成的摘要文本。
        """
        if not self.resources and not self.servers:
            return 'No local MCP manifests, servers, or resources discovered.'
        lines = [
            f'Local MCP resources: {len(self.resources)}',
            f'Configured MCP servers: {len(self.servers)}',
        ]
        for server in self.servers[:10]:
            lines.append(f'- Server: {server.name} ; {server.transport} ; {server.command}')
        return '\n'.join(lines)

    def list_resources(
        self,
        *,
        query: str | None = None,
        server_name: str | None = None,
        limit: int | None = None,
    ) -> tuple[MCPResource, ...]:
        """列出本地与远端 MCP 资源。

        Args:
            query (str | None): 可选查询词，用于过滤资源。
            server_name (str | None): 可选 server 名称，仅保留指定 server 的资源。
            limit (int | None): 最大返回数量；为 None 时不限制。
        Returns:
            tuple[MCPResource, ...]: 过滤后的资源元组。
        Raises:
            ValueError: 当 server_name 非法时抛出。
            MCPTransportError: 当指定 server 的远端资源列举失败时抛出。
        """
        normalized_server_name = normalize_name(server_name, label='server_name') if server_name is not None else None
        local_resources = tuple(
            item for item in self.resources if normalized_server_name is None or item.server_name == normalized_server_name
        )
        remote_resources = self._list_remote_resources(server_name=normalized_server_name)
        combined = self._renderer.filter_resources(local_resources + remote_resources, query=query)
        if limit is not None and limit >= 0:
            combined = combined[:limit]
        return combined

    def get_resource(self, uri: str) -> MCPResource:
        """按 URI 定位单个 MCP 资源定义。

        Args:
            uri (str): 资源 URI。
        Returns:
            MCPResource: 匹配到的资源对象。
        Raises:
            ValueError: 当 URI 非法或资源不存在时抛出。
            MCPTransportError: 当远端资源列举失败且调用方指定了严格 server 约束时抛出。
        """
        normalized_uri = _normalize_uri(uri)
        for resource in self.resources:
            if resource.uri == normalized_uri:
                return resource
        for resource in self._list_remote_resources():
            if resource.uri == normalized_uri:
                return resource
        raise ValueError(f'Unknown MCP resource: {normalized_uri!r}')

    def read_resource(self, uri: str, *, max_chars: int = 12000) -> str:
        """读取指定 MCP 资源的文本内容。

        Args:
            uri (str): 目标资源 URI。
            max_chars (int): 返回文本的最大字符数。
        Returns:
            str: 资源文本内容。
        Raises:
            ValueError: 当 URI 非法时抛出。
            FileNotFoundError: 当资源不存在或本地文件缺失时抛出。
        """
        normalized_uri = _normalize_uri(uri)
        for resource in self.resources:
            if resource.uri != normalized_uri:
                continue
            if resource.inline_text is not None:
                return self._renderer.truncate(resource.inline_text, max_chars)
            if resource.resolved_path is None:
                break
            if not resource.resolved_path.is_file():
                raise FileNotFoundError(f'MCP resource file not found: {resource.resolved_path}')
            return self._renderer.truncate(
                resource.resolved_path.read_text(encoding='utf-8', errors='replace'),
                max_chars,
            )

        last_error: MCPTransportError | None = None
        candidate_servers: list[MCPServerProfile] = []
        try:
            resource = self.get_resource(normalized_uri)
        except ValueError:
            resource = None
        if resource is not None:
            server = self.get_server(resource.server_name)
            if server is not None:
                candidate_servers.append(server)
        for server in self.servers:
            if all(existing.name != server.name for existing in candidate_servers):
                candidate_servers.append(server)

        for server in candidate_servers:
            try:
                result = self._transport_client.request(server, 'resources/read', {'uri': normalized_uri})
            except MCPTransportError as exc:
                last_error = exc
                continue
            rendered = self._renderer.render_resource_contents(result.get('contents'))
            if rendered:
                return self._renderer.truncate(rendered, max_chars)

        if last_error is not None:
            raise FileNotFoundError(f'Unable to read MCP resource {normalized_uri}: {last_error}') from last_error
        raise FileNotFoundError(f'Unknown MCP resource: {normalized_uri!r}')

    def render_resource_index(
        self,
        *,
        query: str | None = None,
        server_name: str | None = None,
        limit: int = 20,
    ) -> str:
        """把资源列表渲染为面向模型的索引文本。

        Args:
            query (str | None): 可选查询词。
            server_name (str | None): 可选 server 过滤条件。
            limit (int): 最多渲染的资源数量。
        Returns:
            str: 资源索引文本。
        Raises:
            ValueError: 当 server_name 非法时抛出。
            MCPTransportError: 当指定 server 的远端资源列举失败时抛出。
        """
        resources = self.list_resources(query=query, server_name=server_name, limit=limit)
        if not resources:
            return '# MCP Resources\n\nNo matching MCP resources discovered.'

        lines = ['# MCP Resources', '']
        for resource in resources:
            details = [resource.uri, f'server={resource.server_name}']
            if resource.name:
                details.append(f'name={resource.name}')
            if resource.mime_type:
                details.append(f'mime={resource.mime_type}')
            if resource.resolved_path is not None:
                details.append(f'path={resource.resolved_path}')
            elif resource.inline_text is not None:
                details.append('source=inline')
            else:
                details.append('source=transport')
            lines.append('- ' + '; '.join(details))
        return '\n'.join(lines)

    def render_resource(self, uri: str, *, max_chars: int = 12000) -> str:
        """把单个资源及其内容渲染为完整文本。

        Args:
            uri (str): 目标资源 URI。
            max_chars (int): 内容部分的最大字符数。
        Returns:
            str: 含资源元信息和正文内容的文本。
        Raises:
            ValueError: 当 URI 非法或资源不存在时抛出。
            FileNotFoundError: 当资源内容无法读取时抛出。
        """
        resource = self.get_resource(uri)
        lines = [
            '# MCP Resource',
            '',
            f'- URI: {resource.uri}',
            f'- Server: {resource.server_name}',
        ]
        if resource.name:
            lines.append(f'- Name: {resource.name}')
        if resource.mime_type:
            lines.append(f'- MIME Type: {resource.mime_type}')
        lines.extend(['', self.read_resource(uri, max_chars=max_chars)])
        return '\n'.join(lines)

    def list_tools(
        self,
        *,
        query: str | None = None,
        server_name: str | None = None,
        limit: int | None = None,
    ) -> tuple[MCPTool, ...]:
        """列出远端 MCP 工具定义。

        Args:
            query (str | None): 可选查询词，用于过滤工具。
            server_name (str | None): 可选 server 名称，仅列出指定 server 的工具。
            limit (int | None): 最大返回数量；为 None 时不限制。
        Returns:
            tuple[MCPTool, ...]: 过滤后的工具元组。
        Raises:
            ValueError: 当 server_name 非法时抛出。
            MCPTransportError: 当指定 server 的工具列举失败时抛出。
        """
        normalized_server_name = normalize_name(server_name, label='server_name') if server_name is not None else None
        tools = self._list_remote_tools(server_name=normalized_server_name)
        if query:
            needle = query.lower()
            tools = tuple(
                tool
                for tool in tools
                if needle in tool.name.lower()
                or needle in tool.server_name.lower()
                or needle in (tool.description or '').lower()
            )
        if limit is not None and limit >= 0:
            tools = tools[:limit]
        return tools

    def call_tool(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        server_name: str | None = None,
        max_chars: int = 12000,
    ) -> MCPToolCallResult:
        """执行指定 MCP 工具并返回归一化结果。

        Args:
            tool_name (str): 目标工具名称。
            arguments (dict[str, Any] | None): 传给远端工具的参数对象。
            server_name (str | None): 可选 server 名称，用于消歧。
            max_chars (int): 渲染后文本结果的最大字符数。
        Returns:
            MCPToolCallResult: 归一化后的工具调用结果。
        Raises:
            ValueError: 当工具名或 server 名不合法，或目标工具无法唯一定位时抛出。
            MCPTransportError: 当远端 tools/call 请求失败时抛出。
        """
        tool = self._resolve_tool(tool_name, server_name=server_name)
        server = self.get_server(tool.server_name)
        if server is None:
            raise ValueError(f'Unknown MCP server: {tool.server_name!r}')

        result = self._transport_client.request(
            server,
            'tools/call',
            {
                'name': tool.name,
                'arguments': dict(arguments or {}),
            },
        )
        return MCPToolCallResult(
            server_name=tool.server_name,
            tool_name=tool.name,
            content=self._renderer.truncate(self._renderer.render_tool_call_result(result), max_chars),
            is_error=bool(result.get('isError')),
            raw_result=dict(result),
        )

    def render_tool_index(
        self,
        *,
        query: str | None = None,
        server_name: str | None = None,
        limit: int = 50,
    ) -> str:
        """把工具列表渲染为面向模型的索引文本。

        Args:
            query (str | None): 可选查询词。
            server_name (str | None): 可选 server 过滤条件。
            limit (int): 最多渲染的工具数量。
        Returns:
            str: 工具索引文本。
        Raises:
            ValueError: 当 server_name 非法时抛出。
            MCPTransportError: 当指定 server 的工具列举失败时抛出。
        """
        tools = self.list_tools(query=query, server_name=server_name, limit=limit)
        if not tools:
            return '# MCP Tools\n\nNo matching MCP tools discovered.'

        lines = ['# MCP Tools', '']
        for tool in tools:
            details = [tool.name, f'server={tool.server_name}']
            if tool.description:
                details.append(tool.description)
            lines.append('- ' + ' ; '.join(details))
        return '\n'.join(lines)

    def render_tool_result(self, result: MCPToolCallResult) -> str:
        """把已有的工具调用结果渲染为完整文本。

        Args:
            result (MCPToolCallResult): 已归一化的工具调用结果对象。
        Returns:
            str: 带工具元信息和正文内容的文本。
        """
        return '\n'.join(
            [
                '# MCP Tool Result',
                '',
                f'- Tool: {result.tool_name}',
                f'- Server: {result.server_name}',
                f'- is_error: {result.is_error}',
                '',
                result.content,
            ]
        )

    def render_tool_call(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        server_name: str | None = None,
        max_chars: int = 12000,
    ) -> str:
        """执行工具并直接返回渲染后的结果文本。

        Args:
            tool_name (str): 目标工具名称。
            arguments (dict[str, Any] | None): 传给远端工具的参数对象。
            server_name (str | None): 可选 server 名称，用于消歧。
            max_chars (int): 渲染后文本结果的最大字符数。
        Returns:
            str: 工具调用的完整文本结果。
        Raises:
            ValueError: 当工具名或 server 名不合法，或目标工具无法唯一定位时抛出。
            MCPTransportError: 当远端 tools/call 请求失败时抛出。
        """
        result = self.call_tool(tool_name, arguments=arguments, server_name=server_name, max_chars=max_chars)
        return self.render_tool_result(result)

    def get_server(self, server_name: str) -> MCPServerProfile | None:
        """按名称获取已加载的 server 配置。

        Args:
            server_name (str): 目标 server 名称。
        Returns:
            MCPServerProfile | None: 匹配到的 server 配置；不存在时返回 None。
        Raises:
            ValueError: 当 server_name 非法时抛出。
        """
        normalized_name = normalize_name(server_name, label='server_name')
        for server in self.servers:
            if server.name == normalized_name:
                return server
        return None

    def _list_remote_resources(self, *, server_name: str | None = None) -> tuple[MCPResource, ...]:
        """列举远端 server 暴露的资源定义。

        Args:
            server_name (str | None): 可选 server 名称过滤条件。
        Returns:
            tuple[MCPResource, ...]: 远端资源元组。
        Raises:
            ValueError: 当 server_name 非法或指定 server 不存在时抛出。
            MCPTransportError: 当指定 server 的远端请求失败时抛出。
        """
        discovered: list[MCPResource] = []
        candidate_servers = self._resolve_candidate_servers(server_name)
        for server in candidate_servers:
            try:
                result = self._transport_client.request(server, 'resources/list', {})
            except MCPTransportError:
                if server_name is not None:
                    raise
                continue
            discovered.extend(_extract_remote_resources(server, result))
        return tuple(discovered)

    def _resolve_candidate_servers(self, server_name: str | None) -> tuple[MCPServerProfile, ...]:
        """把可选的 server 过滤条件解析为待请求 server 列表。

        Args:
            server_name (str | None): 外部传入的可选 server 名称。
        Returns:
            tuple[MCPServerProfile, ...]: 需要参与本次请求的 server 元组。
        Raises:
            ValueError: 当指定的 server 名称不存在或非法时抛出。
        """
        if server_name is None:
            return tuple(self.servers)
        normalized_name = normalize_name(server_name, label='server_name')
        for server in self.servers:
            if server.name == normalized_name:
                return (server,)
        raise ValueError(f'Unknown MCP server: {normalized_name!r}')

    def _list_remote_tools(self, *, server_name: str | None = None) -> tuple[MCPTool, ...]:
        """列举远端 server 暴露的工具定义。

        Args:
            server_name (str | None): 可选 server 名称过滤条件。
        Returns:
            tuple[MCPTool, ...]: 远端工具元组。
        Raises:
            ValueError: 当 server_name 非法或指定 server 不存在时抛出。
            MCPTransportError: 当指定 server 的远端请求失败时抛出。
        """
        discovered: list[MCPTool] = []
        candidate_servers = self._resolve_candidate_servers(server_name)
        for server in candidate_servers:
            try:
                result = self._transport_client.request(server, 'tools/list', {})
            except MCPTransportError:
                if server_name is not None:
                    raise
                continue
            discovered.extend(_extract_remote_tools(server, result))
        return tuple(discovered)

    def _resolve_tool(self, tool_name: str, *, server_name: str | None = None) -> MCPTool:
        """根据工具名和可选 server 条件定位唯一工具。

        Args:
            tool_name (str): 目标工具名称。
            server_name (str | None): 可选 server 名称，用于消歧。
        Returns:
            MCPTool: 唯一匹配的工具定义。
        Raises:
            ValueError: 当工具不存在、server 不存在或工具名冲突无法消歧时抛出。
            MCPTransportError: 当指定 server 的工具列举失败时抛出。
        """
        normalized_name = normalize_name(tool_name, label='tool_name')
        matches = [tool for tool in self.list_tools(server_name=server_name) if tool.name == normalized_name]
        if not matches:
            if server_name is None:
                raise ValueError(f'Unknown MCP tool: {normalized_name!r}')
            raise ValueError(f'Unknown MCP tool: {normalized_name!r} on server {server_name!r}')
        if len(matches) > 1 and server_name is None:
            raise ValueError(
                f'MCP tool {normalized_name!r} exists on multiple servers. Pass server_name to disambiguate.'
            )
        return matches[0]


def _normalize_uri(value: object) -> str:
    """把资源 URI 归一化为非空字符串。

    Args:
        value (object): 待校验的 URI 输入值。
    Returns:
        str: 去除首尾空白后的 URI。
    Raises:
        ValueError: 当输入不是合法的非空字符串时抛出。
    """
    if not isinstance(value, str):
        raise ValueError('uri must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError('uri must not be empty')
    return normalized


def _extract_remote_resources(server: MCPServerProfile, payload: dict[str, Any]) -> tuple[MCPResource, ...]:
    """把 resources/list 返回的远端负载归一化为资源对象。

    Args:
        server (MCPServerProfile): 产生本次结果的 server 配置。
        payload (dict[str, Any]): resources/list 的 result 负载。
    Returns:
        tuple[MCPResource, ...]: 归一化后的资源元组。
    """
    raw_resources = payload.get('resources')
    if not isinstance(raw_resources, list):
        return ()

    server_target = server.command if server.command else (server.url or '')
    resources: list[MCPResource] = []
    for item in raw_resources:
        if not isinstance(item, dict):
            continue
        raw_uri = item.get('uri')
        if not isinstance(raw_uri, str) or not raw_uri.strip():
            continue
        resources.append(
            MCPResource(
                uri=raw_uri.strip(),
                server_name=server.name,
                source_path=server.source_path,
                name=normalize_optional_text(item.get('name')),
                description=normalize_optional_text(item.get('description')),
                mime_type=normalize_optional_text(item.get('mimeType')) or normalize_optional_text(item.get('mime_type')),
                metadata={
                    'transport': server.transport,
                    'server_command': server_target,
                },
            )
        )
    return tuple(resources)


def _extract_remote_tools(server: MCPServerProfile, payload: dict[str, Any]) -> tuple[MCPTool, ...]:
    """把 tools/list 返回的远端负载归一化为工具对象。

    Args:
        server (MCPServerProfile): 产生本次结果的 server 配置。
        payload (dict[str, Any]): tools/list 的 result 负载。
    Returns:
        tuple[MCPTool, ...]: 归一化后的工具元组。
    """
    raw_tools = payload.get('tools')
    if not isinstance(raw_tools, list):
        return ()

    server_target = server.command if server.command else (server.url or '')
    tools: list[MCPTool] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        raw_name = item.get('name')
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        input_schema = item.get('inputSchema') if isinstance(item.get('inputSchema'), dict) else item.get('input_schema')
        tools.append(
            MCPTool(
                name=raw_name.strip(),
                server_name=server.name,
                source_path=server.source_path,
                description=normalize_optional_text(item.get('description')),
                input_schema=dict(input_schema) if isinstance(input_schema, dict) else {},
                metadata={
                    'transport': server.transport,
                    'server_command': server_target,
                },
            )
        )
    return tuple(tools)