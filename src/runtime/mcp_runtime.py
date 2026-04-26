"""ISSUE-021 MCP Runtime：资源发现、工具发现与 stdio transport 调用。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core_contracts.protocol import JSONDict


MCP_PROTOCOL_VERSION = '2025-11-25'
_MCP_MANIFEST_FILE = Path('.claw') / 'mcp.json'
_MCP_MANIFEST_DIR = Path('.claw') / 'mcp'
_SCHEMA_VERSION = 1
_DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class MCPResource:
    """MCP 资源定义。"""

    uri: str
    server_name: str
    source_path: Path | None = None
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None
    resolved_path: Path | None = None
    inline_text: str | None = None
    metadata: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        payload: JSONDict = {
            'uri': self.uri,
            'server_name': self.server_name,
        }
        if self.name is not None:
            payload['name'] = self.name
        if self.description is not None:
            payload['description'] = self.description
        if self.mime_type is not None:
            payload['mime_type'] = self.mime_type
        if self.resolved_path is not None:
            payload['resolved_path'] = str(self.resolved_path)
        if self.inline_text is not None:
            payload['inline_text'] = self.inline_text
        if self.source_path is not None:
            payload['source_path'] = str(self.source_path)
        if self.metadata:
            payload['metadata'] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class MCPTool:
    """MCP 工具定义。"""

    name: str
    server_name: str
    source_path: Path | None = None
    description: str | None = None
    input_schema: JSONDict = field(default_factory=dict)
    metadata: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        payload: JSONDict = {
            'name': self.name,
            'server_name': self.server_name,
            'input_schema': dict(self.input_schema),
        }
        if self.description is not None:
            payload['description'] = self.description
        if self.source_path is not None:
            payload['source_path'] = str(self.source_path)
        if self.metadata:
            payload['metadata'] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class MCPServerProfile:
    """MCP server profile。"""

    name: str
    transport: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    description: str | None = None
    source_path: Path | None = None
    metadata: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        payload: JSONDict = {
            'name': self.name,
            'transport': self.transport,
            'command': self.command,
            'args': list(self.args),
            'env': dict(self.env),
        }
        if self.cwd is not None:
            payload['cwd'] = str(self.cwd)
        if self.description is not None:
            payload['description'] = self.description
        if self.source_path is not None:
            payload['source_path'] = str(self.source_path)
        if self.metadata:
            payload['metadata'] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class MCPToolCallResult:
    """MCP 工具调用结果。"""

    server_name: str
    tool_name: str
    content: str
    is_error: bool
    raw_result: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        return {
            'server_name': self.server_name,
            'tool_name': self.tool_name,
            'content': self.content,
            'is_error': self.is_error,
            'raw_result': dict(self.raw_result),
        }


@dataclass(frozen=True)
class MCPLoadError:
    """manifest 加载错误。"""

    source_path: Path
    detail: str


class MCPTransportError(RuntimeError):
    """stdio MCP transport 失败。"""

    def __init__(
        self,
        *,
        server_name: str,
        method: str,
        detail: str,
        stderr: str = '',
        exit_code: int | None = None,
    ) -> None:
        self.server_name = server_name
        self.method = method
        self.detail = detail
        self.stderr = stderr
        self.exit_code = exit_code

        message = f'MCP transport failure for server {server_name!r} during {method}: {detail}'
        if exit_code is not None:
            message += f' (exit_code={exit_code})'
        if stderr:
            message += f' stderr={stderr}'
        super().__init__(message)


@dataclass
class MCPRuntime:
    """工作区本地 MCP Runtime。"""

    workspace: Path
    resources: tuple[MCPResource, ...] = ()
    servers: tuple[MCPServerProfile, ...] = ()
    load_errors: tuple[MCPLoadError, ...] = ()
    schema_version: int = _SCHEMA_VERSION

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'MCPRuntime':
        resolved_workspace = workspace.resolve()
        resources: list[MCPResource] = []
        servers: list[MCPServerProfile] = []
        load_errors: list[MCPLoadError] = []

        for manifest_path in _discover_manifest_paths(resolved_workspace):
            manifest_resources, manifest_servers, manifest_errors = _load_manifest(manifest_path)
            resources.extend(manifest_resources)
            servers.extend(manifest_servers)
            load_errors.extend(manifest_errors)

        return cls(
            workspace=resolved_workspace,
            resources=tuple(resources),
            servers=tuple(_dedupe_servers(servers)),
            load_errors=tuple(load_errors),
        )

    def list_resources(
        self,
        *,
        query: str | None = None,
        server_name: str | None = None,
        limit: int | None = None,
    ) -> tuple[MCPResource, ...]:
        local_resources = tuple(
            item for item in self.resources if server_name is None or item.server_name == server_name
        )
        remote_resources = self._list_remote_resources(server_name=server_name)
        combined = _filter_resources(local_resources + remote_resources, query=query)
        if limit is not None and limit >= 0:
            combined = combined[:limit]
        return combined

    def get_resource(self, uri: str) -> MCPResource:
        normalized_uri = _normalize_uri(uri)
        for resource in self.resources:
            if resource.uri == normalized_uri:
                return resource
        for resource in self._list_remote_resources():
            if resource.uri == normalized_uri:
                return resource
        raise ValueError(f'Unknown MCP resource: {normalized_uri!r}')

    def read_resource(self, uri: str, *, max_chars: int = 12000) -> str:
        normalized_uri = _normalize_uri(uri)
        for resource in self.resources:
            if resource.uri != normalized_uri:
                continue
            if resource.inline_text is not None:
                return _truncate(resource.inline_text, max_chars)
            if resource.resolved_path is None:
                break
            if not resource.resolved_path.is_file():
                raise FileNotFoundError(f'MCP resource file not found: {resource.resolved_path}')
            return _truncate(resource.resolved_path.read_text(encoding='utf-8', errors='replace'), max_chars)

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
            if server.transport != 'stdio':
                continue
            if all(existing.name != server.name for existing in candidate_servers):
                candidate_servers.append(server)

        for server in candidate_servers:
            try:
                result = _request_stdio(server, 'resources/read', {'uri': normalized_uri})
            except MCPTransportError as exc:
                last_error = exc
                continue
            rendered = _render_resource_contents(result.get('contents'))
            if rendered:
                return _truncate(rendered, max_chars)

        if last_error is not None:
            raise FileNotFoundError(f'Unable to read MCP resource {normalized_uri}: {last_error}') from last_error
        raise FileNotFoundError(f'Unknown MCP resource: {normalized_uri!r}')

    def list_tools(
        self,
        *,
        query: str | None = None,
        server_name: str | None = None,
        limit: int | None = None,
    ) -> tuple[MCPTool, ...]:
        tools = self._list_remote_tools(server_name=server_name)
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
        tool = self._resolve_tool(tool_name, server_name=server_name)
        server = self.get_server(tool.server_name)
        if server is None:
            raise ValueError(f'Unknown MCP server: {tool.server_name!r}')

        result = _request_stdio(
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
            content=_truncate(_render_tool_call_result(result), max_chars),
            is_error=bool(result.get('isError')),
            raw_result=dict(result),
        )

    def get_server(self, server_name: str) -> MCPServerProfile | None:
        normalized_name = _normalize_name(server_name, label='server_name')
        for server in self.servers:
            if server.name == normalized_name:
                return server
        return None

    def render_summary(self) -> str:
        if not self.resources and not self.servers:
            return 'No local MCP manifests, servers, or resources discovered.'
        lines = [
            f'Local MCP resources: {len(self.resources)}',
            f'Configured MCP servers: {len(self.servers)}',
        ]
        for server in self.servers[:10]:
            lines.append(f'- Server: {server.name} ; {server.transport} ; {server.command}')
        return '\n'.join(lines)

    def render_resource_index(self, *, query: str | None = None, limit: int = 20) -> str:
        resources = self.list_resources(query=query, limit=limit)
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

    def render_tool_index(
        self,
        *,
        query: str | None = None,
        server_name: str | None = None,
        limit: int = 50,
    ) -> str:
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

    def render_tool_call(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        server_name: str | None = None,
        max_chars: int = 12000,
    ) -> str:
        result = self.call_tool(tool_name, arguments=arguments, server_name=server_name, max_chars=max_chars)
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

    def _list_remote_resources(self, *, server_name: str | None = None) -> tuple[MCPResource, ...]:
        discovered: list[MCPResource] = []
        candidate_servers = _resolve_candidate_servers(self.servers, server_name)
        for server in candidate_servers:
            if server.transport != 'stdio':
                continue
            try:
                result = _request_stdio(server, 'resources/list', {})
            except MCPTransportError:
                if server_name is not None:
                    raise
                continue
            discovered.extend(_extract_remote_resources(server, result))
        return tuple(discovered)

    def _list_remote_tools(self, *, server_name: str | None = None) -> tuple[MCPTool, ...]:
        discovered: list[MCPTool] = []
        candidate_servers = _resolve_candidate_servers(self.servers, server_name)
        for server in candidate_servers:
            if server.transport != 'stdio':
                continue
            try:
                result = _request_stdio(server, 'tools/list', {})
            except MCPTransportError:
                if server_name is not None:
                    raise
                continue
            discovered.extend(_extract_remote_tools(server, result))
        return tuple(discovered)

    def _resolve_tool(self, tool_name: str, *, server_name: str | None = None) -> MCPTool:
        normalized_name = _normalize_name(tool_name, label='tool_name')
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


def _discover_manifest_paths(workspace: Path) -> tuple[Path, ...]:
    discovered: list[Path] = []
    single_manifest = workspace / _MCP_MANIFEST_FILE
    if single_manifest.is_file():
        discovered.append(single_manifest.resolve())

    manifest_dir = workspace / _MCP_MANIFEST_DIR
    if manifest_dir.is_dir():
        discovered.extend(
            path.resolve()
            for path in sorted(manifest_dir.glob('*.json'))
            if path.is_file()
        )
    return tuple(discovered)


def _load_manifest(path: Path) -> tuple[tuple[MCPResource, ...], tuple[MCPServerProfile, ...], tuple[MCPLoadError, ...]]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return (), (), (MCPLoadError(source_path=path, detail=str(exc)),)

    if not isinstance(payload, dict):
        return (), (), (MCPLoadError(source_path=path, detail='MCP manifest must be a JSON object'),)

    resources: list[MCPResource] = []
    servers: list[MCPServerProfile] = []
    load_errors: list[MCPLoadError] = []

    raw_resources = payload.get('resources')
    if isinstance(raw_resources, list):
        resources.extend(_extract_resources('local', raw_resources, manifest_path=path))

    raw_servers = payload.get('servers')
    if isinstance(raw_servers, list):
        for item in raw_servers:
            if not isinstance(item, dict):
                continue
            raw_name = item.get('name')
            if not isinstance(raw_name, str) or not raw_name.strip():
                load_errors.append(MCPLoadError(source_path=path, detail='Server entry requires non-empty name'))
                continue
            server_name = raw_name.strip()
            if isinstance(item.get('resources'), list):
                resources.extend(_extract_resources(server_name, item['resources'], manifest_path=path))
            try:
                server = _extract_server_profile(server_name, item, manifest_path=path)
            except ValueError as exc:
                load_errors.append(MCPLoadError(source_path=path, detail=str(exc)))
                continue
            if server is not None:
                servers.append(server)

    raw_mcp_servers = payload.get('mcpServers')
    if isinstance(raw_mcp_servers, dict):
        for raw_name, item in raw_mcp_servers.items():
            if not isinstance(raw_name, str) or not raw_name.strip() or not isinstance(item, dict):
                continue
            try:
                server = _extract_server_profile(raw_name.strip(), item, manifest_path=path)
            except ValueError as exc:
                load_errors.append(MCPLoadError(source_path=path, detail=str(exc)))
                continue
            if server is not None:
                servers.append(server)

    return tuple(resources), tuple(servers), tuple(load_errors)


def _extract_server_profile(
    server_name: str,
    payload: dict[str, Any],
    *,
    manifest_path: Path,
) -> MCPServerProfile | None:
    transport = _normalize_optional_text(payload.get('transport')) or 'stdio'
    transport = transport.lower()
    if transport != 'stdio':
        return None

    command = _normalize_optional_text(payload.get('command'))
    if command is None:
        return None

    raw_args = payload.get('args', ())
    if not isinstance(raw_args, list):
        raw_args = []
    args = tuple(item for item in raw_args if isinstance(item, str))

    raw_env = payload.get('env')
    env = {
        key: value
        for key, value in (raw_env.items() if isinstance(raw_env, dict) else [])
        if isinstance(key, str) and isinstance(value, str)
    }

    raw_cwd = _normalize_optional_text(payload.get('cwd'))
    resolved_cwd: Path | None = None
    if raw_cwd is not None:
        resolved_cwd = _resolve_manifest_path(manifest_path, raw_cwd)

    return MCPServerProfile(
        name=_normalize_name(server_name, label='server_name'),
        transport='stdio',
        command=command,
        args=args,
        env=env,
        cwd=resolved_cwd,
        description=_normalize_optional_text(payload.get('description')),
        source_path=manifest_path.resolve(),
        metadata=dict(payload.get('metadata')) if isinstance(payload.get('metadata'), dict) else {},
    )


def _extract_resources(server_name: str, raw_resources: list[Any], *, manifest_path: Path) -> tuple[MCPResource, ...]:
    resources: list[MCPResource] = []
    seen_uris: set[str] = set()
    normalized_server_name = _normalize_name(server_name, label='server_name')
    for item in raw_resources:
        if not isinstance(item, dict):
            continue
        raw_uri = item.get('uri')
        if not isinstance(raw_uri, str) or not raw_uri.strip():
            continue
        uri = raw_uri.strip()
        if uri in seen_uris:
            continue
        seen_uris.add(uri)

        raw_path = _normalize_optional_text(item.get('path')) or _normalize_optional_text(item.get('file'))
        resolved_path: Path | None = None
        if raw_path is not None:
            resolved_path = _resolve_manifest_path(manifest_path, raw_path)

        resources.append(
            MCPResource(
                uri=uri,
                server_name=normalized_server_name,
                source_path=manifest_path.resolve(),
                name=_normalize_optional_text(item.get('name')),
                description=_normalize_optional_text(item.get('description')),
                mime_type=_normalize_optional_text(item.get('mimeType')) or _normalize_optional_text(item.get('mime_type')),
                resolved_path=resolved_path,
                inline_text=_normalize_optional_text(item.get('text')),
                metadata=dict(item.get('metadata')) if isinstance(item.get('metadata'), dict) else {},
            )
        )
    return tuple(resources)


def _extract_remote_resources(server: MCPServerProfile, payload: dict[str, Any]) -> tuple[MCPResource, ...]:
    raw_resources = payload.get('resources')
    if not isinstance(raw_resources, list):
        return ()

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
                name=_normalize_optional_text(item.get('name')),
                description=_normalize_optional_text(item.get('description')),
                mime_type=_normalize_optional_text(item.get('mimeType')) or _normalize_optional_text(item.get('mime_type')),
                metadata={
                    'transport': server.transport,
                    'server_command': server.command,
                },
            )
        )
    return tuple(resources)


def _extract_remote_tools(server: MCPServerProfile, payload: dict[str, Any]) -> tuple[MCPTool, ...]:
    raw_tools = payload.get('tools')
    if not isinstance(raw_tools, list):
        return ()

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
                description=_normalize_optional_text(item.get('description')),
                input_schema=dict(input_schema) if isinstance(input_schema, dict) else {},
                metadata={
                    'transport': server.transport,
                    'server_command': server.command,
                },
            )
        )
    return tuple(tools)


def _dedupe_servers(servers: list[MCPServerProfile] | tuple[MCPServerProfile, ...]) -> tuple[MCPServerProfile, ...]:
    deduped: list[MCPServerProfile] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for server in servers:
        key = (server.name.lower(), server.transport, server.command, server.args)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(server)
    return tuple(deduped)


def _resolve_candidate_servers(
    servers: tuple[MCPServerProfile, ...],
    server_name: str | None,
) -> tuple[MCPServerProfile, ...]:
    if server_name is None:
        return servers
    normalized_name = _normalize_name(server_name, label='server_name')
    for server in servers:
        if server.name == normalized_name:
            return (server,)
    raise ValueError(f'Unknown MCP server: {normalized_name!r}')


def _request_stdio(
    server: MCPServerProfile,
    method: str,
    params: dict[str, Any],
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    command = [server.command, *server.args]
    env = os.environ.copy()
    env.update(server.env)
    payload = b''.join(
        [
            _encode_mcp_message(
                {
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': 'initialize',
                    'params': {
                        'protocolVersion': MCP_PROTOCOL_VERSION,
                        'capabilities': {},
                        'clientInfo': {
                            'name': 'claw-code-agent',
                            'version': '0.1.0',
                        },
                    },
                }
            ),
            _encode_mcp_message(
                {
                    'jsonrpc': '2.0',
                    'method': 'notifications/initialized',
                    'params': {},
                }
            ),
            _encode_mcp_message(
                {
                    'jsonrpc': '2.0',
                    'id': 2,
                    'method': method,
                    'params': params,
                }
            ),
        ]
    )

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(server.cwd) if server.cwd is not None else None,
            env=env,
        )
    except OSError as exc:
        raise MCPTransportError(
            server_name=server.name,
            method=method,
            detail=f'Failed to spawn MCP server: {exc}',
        ) from exc

    try:
        stdout_data, stderr_data = process.communicate(input=payload, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        stdout_data, stderr_data = process.communicate()
        raise MCPTransportError(
            server_name=server.name,
            method=method,
            detail='Timed out waiting for MCP response',
            stderr=_decode_stderr(stderr_data),
            exit_code=process.returncode,
        ) from exc

    stderr_text = _decode_stderr(stderr_data)
    responses = _decode_mcp_messages(stdout_data)
    initialize_response = _find_response(responses, 1)
    if initialize_response is None:
        raise MCPTransportError(
            server_name=server.name,
            method=method,
            detail='Missing initialize response',
            stderr=stderr_text,
            exit_code=process.returncode,
        )

    initialize_error = initialize_response.get('error') if isinstance(initialize_response, dict) else None
    if isinstance(initialize_error, dict):
        raise MCPTransportError(
            server_name=server.name,
            method='initialize',
            detail=str(initialize_error.get('message') or initialize_error),
            stderr=stderr_text,
            exit_code=process.returncode,
        )

    response = _find_response(responses, 2)
    if response is None:
        raise MCPTransportError(
            server_name=server.name,
            method=method,
            detail='Missing method response',
            stderr=stderr_text,
            exit_code=process.returncode,
        )

    response_error = response.get('error') if isinstance(response, dict) else None
    if isinstance(response_error, dict):
        raise MCPTransportError(
            server_name=server.name,
            method=method,
            detail=str(response_error.get('message') or response_error),
            stderr=stderr_text,
            exit_code=process.returncode,
        )

    result = response.get('result') if isinstance(response, dict) else None
    if not isinstance(result, dict):
        return {}
    return result


def _encode_mcp_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=True).encode('utf-8')
    header = f'Content-Length: {len(body)}\r\n\r\n'.encode('ascii')
    return header + body


def _decode_mcp_messages(raw: bytes | None) -> tuple[dict[str, Any], ...]:
    if not raw:
        return ()

    messages: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(raw):
        header_end = raw.find(b'\r\n\r\n', cursor)
        if header_end == -1:
            break
        header_blob = raw[cursor:header_end].decode('ascii', errors='replace')
        cursor = header_end + 4
        content_length = _parse_content_length(header_blob)
        if content_length <= 0:
            break
        body = raw[cursor:cursor + content_length]
        if len(body) < content_length:
            break
        cursor += content_length
        try:
            payload = json.loads(body.decode('utf-8', errors='replace'))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            messages.append(payload)
    return tuple(messages)


def _parse_content_length(header_blob: str) -> int:
    for raw_line in header_blob.split('\r\n'):
        name, _, value = raw_line.partition(':')
        if name.lower() != 'content-length':
            continue
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _find_response(messages: tuple[dict[str, Any], ...], request_id: int) -> dict[str, Any] | None:
    for message in messages:
        if message.get('id') == request_id:
            return message
    return None


def _render_resource_contents(contents: Any) -> str:
    if not isinstance(contents, list):
        return ''

    rendered: list[str] = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        text = item.get('text')
        if isinstance(text, str):
            rendered.append(text)
            continue
        blob = item.get('blob')
        if isinstance(blob, str):
            mime_type = item.get('mimeType') if isinstance(item.get('mimeType'), str) else 'application/octet-stream'
            rendered.append(f'[blob:{mime_type}] {blob}')
            continue
        rendered.append(json.dumps(item, ensure_ascii=True, indent=2))
    return '\n\n'.join(part for part in rendered if part).strip()


def _render_tool_call_result(result: dict[str, Any]) -> str:
    content = result.get('content')
    if not isinstance(content, list):
        return json.dumps(result, ensure_ascii=True, indent=2)

    rendered: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get('text')
        if isinstance(text, str):
            rendered.append(text)
            continue
        rendered.append(json.dumps(item, ensure_ascii=True, indent=2))
    return '\n\n'.join(part for part in rendered if part).strip()


def _filter_resources(resources: tuple[MCPResource, ...], *, query: str | None = None) -> tuple[MCPResource, ...]:
    if not query:
        return resources
    needle = query.lower()
    return tuple(
        resource
        for resource in resources
        if needle in resource.uri.lower()
        or needle in resource.server_name.lower()
        or needle in (resource.name or '').lower()
        or needle in (resource.description or '').lower()
    )


def _normalize_name(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{label} must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{label} must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid {label}: {value!r}')
    return normalized


def _normalize_uri(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('uri must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError('uri must not be empty')
    return normalized


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _decode_stderr(raw: bytes | None) -> str:
    if not raw:
        return ''
    return raw.decode('utf-8', errors='replace').strip()


def _resolve_manifest_path(manifest_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    workspace_root = _infer_workspace_root(manifest_path)
    if workspace_root is not None:
        return (workspace_root / candidate).resolve()
    return (manifest_path.parent / candidate).resolve()


def _infer_workspace_root(manifest_path: Path) -> Path | None:
    parent = manifest_path.parent.resolve()
    if parent.name == '.claw':
        return parent.parent.resolve()
    if parent.name == 'mcp' and parent.parent.name == '.claw':
        return parent.parent.parent.resolve()
    return None


def _truncate(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars] + '...'