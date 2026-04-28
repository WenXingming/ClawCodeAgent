"""负责 MCP manifest 的发现、解析与归一化加载。

本模块在工作区内查找 .claw 下的 MCP 配置文件，把原始 JSON 解析成资源定义、server 配置和加载错误三类结构化对象，供运行时直接消费，同时尽量把路径解析、字段规范化和去重逻辑局部收束在 loader 邻域。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .mcp_models import MCPLoadError, MCPResource, MCPServerProfile


MCP_SCHEMA_VERSION = 1  # int: 当前支持的 MCP manifest schema 版本。
_MCP_MANIFEST_FILE = Path('.claw') / 'mcp.json'  # Path: 工作区级单文件 MCP manifest 位置。
_MCP_MANIFEST_DIR = Path('.claw') / 'mcp'  # Path: 多文件 MCP manifest 目录位置。


class MCPManifestLoader:
    """负责从工作区发现并加载 MCP manifest。

    外部通常只需要传入工作区根目录，然后调用 load 获取资源、server 与
    错误列表。具体的 manifest 查找、字段归一化、路径解析和 server 去重，
    都在该类及其邻近辅助函数中完成。
    """

    def __init__(self, workspace: Path) -> None:
        """初始化加载器。

        Args:
            workspace (Path): 工作区根目录。
        Returns:
            None: 该方法初始化规范化后的工作区根路径。
        """
        self.workspace = workspace.resolve()  # Path: 规范化后的工作区根目录。

    def load(self) -> tuple[tuple[MCPResource, ...], tuple[MCPServerProfile, ...], tuple[MCPLoadError, ...]]:
        """加载当前工作区下全部 MCP manifest。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[tuple[MCPResource, ...], tuple[MCPServerProfile, ...], tuple[MCPLoadError, ...]]:
                依次返回资源、按连接维度去重后的 server 配置以及加载错误列表。
        """
        resources: list[MCPResource] = []
        servers: list[MCPServerProfile] = []
        load_errors: list[MCPLoadError] = []

        for manifest_path in self._discover_manifest_paths():
            manifest_resources, manifest_servers, manifest_errors = _load_manifest(manifest_path)
            resources.extend(manifest_resources)
            servers.extend(manifest_servers)
            load_errors.extend(manifest_errors)

        return tuple(resources), tuple(dedupe_servers(servers)), tuple(load_errors)

    def _discover_manifest_paths(self) -> tuple[Path, ...]:
        """发现工作区内所有 MCP manifest 文件。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[Path, ...]: 按稳定顺序排列、且已解析为绝对路径的 manifest 文件元组。
        """
        discovered: list[Path] = []
        single_manifest = self.workspace / _MCP_MANIFEST_FILE
        if single_manifest.is_file():
            discovered.append(single_manifest.resolve())

        manifest_dir = self.workspace / _MCP_MANIFEST_DIR
        if manifest_dir.is_dir():
            discovered.extend(
                path.resolve()
                for path in sorted(manifest_dir.glob('*.json'))
                if path.is_file()
            )
        return tuple(discovered)


def _load_manifest(path: Path) -> tuple[tuple[MCPResource, ...], tuple[MCPServerProfile, ...], tuple[MCPLoadError, ...]]:
    """解析单个 MCP manifest 文件。

    Args:
        path (Path): 待解析的 manifest 路径。
    Returns:
        tuple[tuple[MCPResource, ...], tuple[MCPServerProfile, ...], tuple[MCPLoadError, ...]]:
            依次返回资源、server 配置和解析错误。
    """
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


def _extract_resources(server_name: str, raw_resources: list[Any], *, manifest_path: Path) -> tuple[MCPResource, ...]:
    """从 manifest 片段中提取资源定义。

    Args:
        server_name (str): 当前资源所属的 server 名称。
        raw_resources (list[Any]): manifest 中的原始 resources 数组。
        manifest_path (Path): 当前 manifest 文件路径。
    Returns:
        tuple[MCPResource, ...]: 归一化后的资源对象元组。
    Raises:
        ValueError: 当 server_name 不合法时抛出。
    """
    resources: list[MCPResource] = []
    seen_uris: set[str] = set()
    normalized_server_name = normalize_name(server_name, label='server_name')

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

        raw_path = normalize_optional_text(item.get('path')) or normalize_optional_text(item.get('file'))
        resolved_path: Path | None = None
        if raw_path is not None:
            resolved_path = resolve_manifest_path(manifest_path, raw_path)

        resources.append(
            MCPResource(
                uri=uri,
                server_name=normalized_server_name,
                source_path=manifest_path.resolve(),
                name=normalize_optional_text(item.get('name')),
                description=normalize_optional_text(item.get('description')),
                mime_type=normalize_optional_text(item.get('mimeType')) or normalize_optional_text(item.get('mime_type')),
                resolved_path=resolved_path,
                inline_text=normalize_optional_text(item.get('text')),
                metadata=dict(item.get('metadata')) if isinstance(item.get('metadata'), dict) else {},
            )
        )

    return tuple(resources)


def normalize_name(value: object, *, label: str) -> str:
    """把名称字段校验并归一化为非空标识符。

    Args:
        value (object): 待校验的输入值。
        label (str): 错误提示中使用的字段名。
    Returns:
        str: 去除首尾空白后的规范名称。
    Raises:
        ValueError: 当输入不是合法的非空名称时抛出。
    """
    if not isinstance(value, str):
        raise ValueError(f'{label} must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{label} must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid {label}: {value!r}')
    return normalized


def normalize_optional_text(value: object) -> str | None:
    """把可选文本字段归一化为去空白后的字符串。

    Args:
        value (object): 原始字段值。
    Returns:
        str | None: 归一化后的文本；为空时返回 None。
    """
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def resolve_manifest_path(manifest_path: Path, raw_path: str) -> Path:
    """把 manifest 中的相对路径解析为绝对路径。

    Args:
        manifest_path (Path): 当前 manifest 文件路径。
        raw_path (str): manifest 中声明的原始路径。
    Returns:
        Path: 优先基于工作区根目录、其次基于 manifest 所在目录解析出的绝对路径。
    """
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    workspace_root = _infer_workspace_root(manifest_path)
    if workspace_root is not None:
        return (workspace_root / candidate).resolve()
    return (manifest_path.parent / candidate).resolve()


def _infer_workspace_root(manifest_path: Path) -> Path | None:
    """根据 manifest 所在层级推断工作区根目录。

    Args:
        manifest_path (Path): 当前 manifest 文件路径。
    Returns:
        Path | None: 推断出的工作区根目录；当 manifest 不在约定层级时返回 None。
    """
    parent = manifest_path.parent.resolve()
    if parent.name == '.claw':
        return parent.parent.resolve()
    if parent.name == 'mcp' and parent.parent.name == '.claw':
        return parent.parent.parent.resolve()
    return None


def _extract_server_profile(
    server_name: str,
    payload: dict[str, Any],
    *,
    manifest_path: Path,
) -> MCPServerProfile | None:
    """从 manifest 片段中提取单个 server 配置。

    Args:
        server_name (str): 当前 server 名称。
        payload (dict[str, Any]): 原始 server 配置字典。
        manifest_path (Path): 当前 manifest 文件路径。
    Returns:
        MCPServerProfile | None: 归一化后的 server 配置；transport 不支持或关键信息缺失时返回 None。
    Raises:
        ValueError: 当名称字段不合法时抛出。
    """
    transport = (normalize_optional_text(payload.get('transport')) or 'stdio').lower()
    if transport not in {'stdio', 'streamable-http', 'sse'}:
        return None

    url = normalize_optional_text(payload.get('url'))
    command = normalize_optional_text(payload.get('command')) or ''
    if transport == 'stdio' and not command:
        return None
    if transport in {'streamable-http', 'sse'} and url is None:
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

    raw_headers = payload.get('headers')
    headers = {
        key: value
        for key, value in (raw_headers.items() if isinstance(raw_headers, dict) else [])
        if isinstance(key, str) and isinstance(value, str)
    }

    raw_cwd = normalize_optional_text(payload.get('cwd'))
    resolved_cwd: Path | None = None
    if raw_cwd is not None:
        resolved_cwd = resolve_manifest_path(manifest_path, raw_cwd)

    return MCPServerProfile(
        name=normalize_name(server_name, label='server_name'),
        transport=transport,
        command=command,
        url=url,
        args=args,
        headers=headers,
        env=env,
        cwd=resolved_cwd,
        description=normalize_optional_text(payload.get('description')),
        source_path=manifest_path.resolve(),
        metadata=dict(payload.get('metadata')) if isinstance(payload.get('metadata'), dict) else {},
    )


def dedupe_servers(servers: list[MCPServerProfile] | tuple[MCPServerProfile, ...]) -> tuple[MCPServerProfile, ...]:
    """按 transport 和命令维度去重 server 配置。

    Args:
        servers (list[MCPServerProfile] | tuple[MCPServerProfile, ...]): 原始 server 配置序列。
    Returns:
        tuple[MCPServerProfile, ...]: 以名称、transport、命令和参数为键去重后的 server 配置元组。
    """
    deduped: list[MCPServerProfile] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for server in servers:
        key = (server.name.lower(), server.transport, server.command, server.args)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(server)
    return tuple(deduped)