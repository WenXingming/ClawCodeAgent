"""ISSUE-014 Plugin Runtime：manifest 发现、alias/virtual 注册与摘要渲染。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from core_contracts.protocol import JSONDict
from tools.agent_tools import AgentTool, ToolExecutionContext


_PLUGIN_MANIFEST_FILE = Path('.claw') / 'plugins.json'
_PLUGIN_MANIFEST_DIR = Path('.claw') / 'plugins'
_EMPTY_OBJECT_SCHEMA: JSONDict = {'type': 'object', 'properties': {}}


@dataclass(frozen=True)
class AliasToolSpec:
    """manifest 中的 alias tool 定义。"""

    name: str
    target: str
    description: str = ''
    arguments: JSONDict = field(default_factory=dict)
    parameters: JSONDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'AliasToolSpec':
        data = dict(payload or {})
        name = str(data.get('name', '')).strip()
        target = str(data.get('target', '')).strip()
        if not name:
            raise ValueError('Alias tool requires non-empty name')
        if not target:
            raise ValueError(f'Alias tool {name!r} requires non-empty target')

        arguments = data.get('arguments', {})
        parameters = data.get('parameters', {})
        if not isinstance(arguments, dict):
            raise ValueError(f'Alias tool {name!r} arguments must be an object')
        if parameters and not isinstance(parameters, dict):
            raise ValueError(f'Alias tool {name!r} parameters must be an object')

        return cls(
            name=name,
            target=target,
            description=str(data.get('description', '')).strip(),
            arguments=dict(arguments),
            parameters=dict(parameters),
        )


@dataclass(frozen=True)
class VirtualToolSpec:
    """manifest 中的 virtual tool 定义。"""

    name: str
    description: str
    content: str
    parameters: JSONDict = field(default_factory=dict)
    metadata: JSONDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'VirtualToolSpec':
        data = dict(payload or {})
        name = str(data.get('name', '')).strip()
        description = str(data.get('description', '')).strip()
        content = str(data.get('content', '')).strip()
        if not name:
            raise ValueError('Virtual tool requires non-empty name')
        if not description:
            raise ValueError(f'Virtual tool {name!r} requires description')
        if not content:
            raise ValueError(f'Virtual tool {name!r} requires content')

        parameters = data.get('parameters', {})
        metadata = data.get('metadata', {})
        if parameters and not isinstance(parameters, dict):
            raise ValueError(f'Virtual tool {name!r} parameters must be an object')
        if metadata and not isinstance(metadata, dict):
            raise ValueError(f'Virtual tool {name!r} metadata must be an object')

        return cls(
            name=name,
            description=description,
            content=content,
            parameters=dict(parameters),
            metadata=dict(metadata),
        )


@dataclass(frozen=True)
class PluginManifest:
    """单个插件清单。"""

    name: str
    summary: str = ''
    deny_tools: tuple[str, ...] = ()
    deny_prefixes: tuple[str, ...] = ()
    before_hooks: tuple[JSONDict, ...] = ()
    after_hooks: tuple[JSONDict, ...] = ()
    aliases: tuple[AliasToolSpec, ...] = ()
    virtual_tools: tuple[VirtualToolSpec, ...] = ()
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, payload: JSONDict | None, *, source_path: Path | None = None) -> 'PluginManifest':
        data = dict(payload or {})
        name = str(data.get('name', '')).strip()
        if not name:
            raise ValueError('Plugin manifest requires non-empty name')

        aliases_raw = data.get('aliases', [])
        virtual_tools_raw = data.get('virtual_tools', data.get('virtualTools', []))
        if not isinstance(aliases_raw, list):
            raise ValueError(f'Plugin manifest {name!r} aliases must be a list')
        if not isinstance(virtual_tools_raw, list):
            raise ValueError(f'Plugin manifest {name!r} virtual_tools must be a list')

        deny_tools = _normalize_string_list(data.get('deny_tools', data.get('denyTools', [])))
        deny_prefixes = _normalize_string_list(data.get('deny_prefixes', data.get('denyPrefixes', [])))
        before_hooks = _normalize_hook_list(data.get('before_hooks', data.get('beforeHooks', [])))
        after_hooks = _normalize_hook_list(data.get('after_hooks', data.get('afterHooks', [])))

        aliases = tuple(
            AliasToolSpec.from_dict(item)
            for item in aliases_raw
            if isinstance(item, dict)
        )
        virtual_tools = tuple(
            VirtualToolSpec.from_dict(item)
            for item in virtual_tools_raw
            if isinstance(item, dict)
        )
        _validate_declared_tool_names(name, aliases=aliases, virtual_tools=virtual_tools)
        return cls(
            name=name,
            summary=str(data.get('summary', '')).strip(),
            deny_tools=deny_tools,
            deny_prefixes=deny_prefixes,
            before_hooks=before_hooks,
            after_hooks=after_hooks,
            aliases=aliases,
            virtual_tools=virtual_tools,
            source_path=source_path.resolve() if source_path else None,
        )

    @classmethod
    def from_path(cls, manifest_path: Path) -> 'PluginManifest':
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Plugin manifest {manifest_path} must contain a JSON object')
        return cls.from_dict(payload, source_path=manifest_path)


@dataclass(frozen=True)
class PluginRegistration:
    """已注册的插件工具。"""

    tool_name: str
    plugin_name: str
    tool_kind: str
    target_name: str = ''


@dataclass(frozen=True)
class PluginConflict:
    """插件工具注册冲突。"""

    tool_name: str
    plugin_name: str
    existing_source: str


@dataclass(frozen=True)
class PluginLoadError:
    """插件 manifest 加载或注册错误。"""

    plugin_name: str
    error: str
    source_path: Path | None = None


@dataclass
class PluginRuntime:
    """工作区插件运行时快照。"""

    manifests: tuple[PluginManifest, ...] = ()
    plugin_registry: dict[str, AgentTool] = field(default_factory=dict)
    registrations: tuple[PluginRegistration, ...] = ()
    conflicts: tuple[PluginConflict, ...] = ()
    load_errors: tuple[PluginLoadError, ...] = ()

    @classmethod
    def from_workspace(
        cls,
        workspace: Path,
        base_tool_registry: Mapping[str, AgentTool],
    ) -> 'PluginRuntime':
        manifests: list[PluginManifest] = []
        load_errors: list[PluginLoadError] = []
        for manifest_path in _discover_manifest_paths(workspace.resolve()):
            try:
                manifests.append(PluginManifest.from_path(manifest_path))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                load_errors.append(
                    PluginLoadError(
                        plugin_name=manifest_path.stem,
                        error=str(exc),
                        source_path=manifest_path,
                    )
                )

        occupied_registry = dict(base_tool_registry)
        plugin_registry: dict[str, AgentTool] = {}
        registrations: list[PluginRegistration] = []
        conflicts: list[PluginConflict] = []

        for manifest in manifests:
            for virtual_tool in manifest.virtual_tools:
                if virtual_tool.name in occupied_registry:
                    conflicts.append(
                        PluginConflict(
                            tool_name=virtual_tool.name,
                            plugin_name=manifest.name,
                            existing_source=_describe_tool_source(virtual_tool.name, base_tool_registry, plugin_registry),
                        )
                    )
                    continue

                tool = _build_virtual_tool(manifest, virtual_tool)
                plugin_registry[tool.name] = tool
                occupied_registry[tool.name] = tool
                registrations.append(
                    PluginRegistration(
                        tool_name=tool.name,
                        plugin_name=manifest.name,
                        tool_kind='virtual',
                    )
                )

            for alias in manifest.aliases:
                if alias.name in occupied_registry:
                    conflicts.append(
                        PluginConflict(
                            tool_name=alias.name,
                            plugin_name=manifest.name,
                            existing_source=_describe_tool_source(alias.name, base_tool_registry, plugin_registry),
                        )
                    )
                    continue

                target_tool = occupied_registry.get(alias.target)
                if target_tool is None:
                    load_errors.append(
                        PluginLoadError(
                            plugin_name=manifest.name,
                            error=(
                                f'Alias tool {alias.name!r} targets unknown tool {alias.target!r}'
                            ),
                            source_path=manifest.source_path,
                        )
                    )
                    continue

                tool = _build_alias_tool(manifest, alias, target_tool)
                plugin_registry[tool.name] = tool
                occupied_registry[tool.name] = tool
                registrations.append(
                    PluginRegistration(
                        tool_name=tool.name,
                        plugin_name=manifest.name,
                        tool_kind='alias',
                        target_name=alias.target,
                    )
                )

        return cls(
            manifests=tuple(manifests),
            plugin_registry=plugin_registry,
            registrations=tuple(registrations),
            conflicts=tuple(conflicts),
            load_errors=tuple(load_errors),
        )

    def merge_tool_registry(self, base_tool_registry: Mapping[str, AgentTool]) -> dict[str, AgentTool]:
        merged = dict(base_tool_registry)
        merged.update(self.plugin_registry)
        return merged

    def resolve_block(self, tool_name: str) -> JSONDict | None:
        if not self.manifests:
            return None
        for manifest in self.manifests:
            if tool_name in manifest.deny_tools:
                return {
                    'source': 'plugin',
                    'source_name': manifest.name,
                    'reason': 'deny_tools',
                    'message': f'Tool {tool_name} blocked by plugin {manifest.name}.',
                }
            for prefix in manifest.deny_prefixes:
                if tool_name.startswith(prefix):
                    return {
                        'source': 'plugin',
                        'source_name': manifest.name,
                        'reason': 'deny_prefixes',
                        'message': f'Tool {tool_name} blocked by plugin {manifest.name}.',
                    }
        return None

    def get_before_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        return self._collect_hooks('before', tool_name)

    def get_after_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        return self._collect_hooks('after', tool_name)

    def render_summary(self) -> str:
        if not self.manifests and not self.load_errors:
            return ''

        lines = ['Discovered Plugins', '==================']
        if not self.manifests:
            lines.append('(none)')

        for manifest in self.manifests:
            lines.append(f'{manifest.name} - {manifest.summary or "No summary provided."}')
            registered_names = [
                item.tool_name
                for item in self.registrations
                if item.plugin_name == manifest.name
            ]
            if registered_names:
                lines.append(f'  tools: {", ".join(registered_names)}')
            else:
                lines.append('  tools: (none registered)')

        if self.conflicts:
            lines.extend(['', 'Conflicts', '---------'])
            for conflict in self.conflicts:
                lines.append(
                    (
                        f'skipped {conflict.tool_name} from {conflict.plugin_name} '
                        f'because it conflicts with {conflict.existing_source}'
                    )
                )

        if self.load_errors:
            lines.extend(['', 'Load Errors', '-----------'])
            for item in self.load_errors:
                location = f' ({item.source_path})' if item.source_path else ''
                lines.append(f'{item.plugin_name}{location}: {item.error}')

        return '\n'.join(lines)

    def _collect_hooks(self, phase: str, tool_name: str) -> tuple[JSONDict, ...]:
        if not self.manifests:
            return ()
        hooks: list[JSONDict] = []
        for manifest in self.manifests:
            raw_hooks = manifest.before_hooks if phase == 'before' else manifest.after_hooks
            for hook in raw_hooks:
                if hook.get('kind') != 'message':
                    continue
                content = str(hook.get('content', '')).strip()
                if not content:
                    continue
                hooks.append(
                    {
                        'phase': phase,
                        'content': content,
                        'tool_name': tool_name,
                        'source': 'plugin',
                        'source_name': manifest.name,
                    }
                )
        return tuple(hooks)


def _discover_manifest_paths(workspace: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    single_manifest = workspace / _PLUGIN_MANIFEST_FILE
    if single_manifest.is_file():
        candidates.append(single_manifest)

    manifest_dir = workspace / _PLUGIN_MANIFEST_DIR
    if manifest_dir.is_dir():
        candidates.extend(
            path.resolve()
            for path in sorted(manifest_dir.glob('*.json'))
            if path.is_file()
        )
    return tuple(candidates)


def _normalize_string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            result.append(normalized)
    return tuple(result)


def _normalize_hook_list(value: object) -> tuple[JSONDict, ...]:
    if not isinstance(value, list):
        return ()
    hooks: list[JSONDict] = []
    for item in value:
        if isinstance(item, dict):
            hooks.append(dict(item))
    return tuple(hooks)


def _validate_declared_tool_names(
    plugin_name: str,
    *,
    aliases: tuple[AliasToolSpec, ...],
    virtual_tools: tuple[VirtualToolSpec, ...],
) -> None:
    seen: set[str] = set()
    for tool_name in [item.name for item in virtual_tools] + [item.name for item in aliases]:
        if tool_name in seen:
            raise ValueError(f'Plugin manifest {plugin_name!r} declares duplicate tool {tool_name!r}')
        seen.add(tool_name)


def _build_alias_tool(manifest: PluginManifest, alias: AliasToolSpec, target_tool: AgentTool) -> AgentTool:
    def _handler(arguments: JSONDict, context: ToolExecutionContext):
        resolved_arguments = dict(arguments)
        resolved_arguments.update(alias.arguments)
        result = target_tool.handler(resolved_arguments, context)
        if isinstance(result, tuple):
            content, metadata = result
        else:
            content, metadata = result, {}

        rendered_metadata = dict(metadata)
        rendered_metadata.update(
            {
                'plugin_name': manifest.name,
                'plugin_tool_kind': 'alias',
                'plugin_tool_target': alias.target,
            }
        )
        return content, rendered_metadata

    return AgentTool(
        name=alias.name,
        description=alias.description or f'Alias for {alias.target} from plugin {manifest.name}.',
        parameters=alias.parameters or _derive_alias_parameters(target_tool.parameters, alias.arguments),
        handler=_handler,
    )


def _build_virtual_tool(manifest: PluginManifest, virtual_tool: VirtualToolSpec) -> AgentTool:
    def _handler(arguments: JSONDict, context: ToolExecutionContext):
        metadata = dict(virtual_tool.metadata)
        metadata.update(
            {
                'plugin_name': manifest.name,
                'plugin_tool_kind': 'virtual',
            }
        )
        return virtual_tool.content, metadata

    return AgentTool(
        name=virtual_tool.name,
        description=virtual_tool.description,
        parameters=virtual_tool.parameters or dict(_EMPTY_OBJECT_SCHEMA),
        handler=_handler,
    )


def _derive_alias_parameters(target_parameters: JSONDict, forced_arguments: JSONDict) -> JSONDict:
    if target_parameters.get('type') != 'object':
        return dict(_EMPTY_OBJECT_SCHEMA)

    schema = dict(target_parameters)
    raw_properties = target_parameters.get('properties', {})
    properties = dict(raw_properties) if isinstance(raw_properties, dict) else {}
    for key in forced_arguments:
        properties.pop(key, None)
    schema['properties'] = properties

    raw_required = target_parameters.get('required', [])
    required = [
        item
        for item in raw_required
        if isinstance(item, str) and item not in forced_arguments
    ]
    if required:
        schema['required'] = required
    else:
        schema.pop('required', None)
    return schema


def _describe_tool_source(
    tool_name: str,
    base_tool_registry: Mapping[str, AgentTool],
    plugin_registry: Mapping[str, AgentTool],
) -> str:
    if tool_name in base_tool_registry:
        return f'core tool {tool_name}'
    if tool_name in plugin_registry:
        return f'plugin tool {tool_name}'
    return f'existing tool {tool_name}'
