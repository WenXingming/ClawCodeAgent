"""管理插件清单发现、alias/virtual 工具注册与摘要渲染。

本模块负责从工作区发现插件清单，解析 alias 与 virtual 工具定义，处理注册冲突，并把插件提供的工具、hooks 与拦截规则暴露给上层运行时使用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from core_contracts.primitives import JSONDict
from core_contracts.tools_contracts import ToolDescriptor, ToolExecutionContext


_PLUGIN_MANIFEST_FILE = Path('.claw') / 'plugins.json'
_PLUGIN_MANIFEST_DIR = Path('.claw') / 'plugins'
_EMPTY_OBJECT_SCHEMA: JSONDict = {'type': 'object', 'properties': {}}


@dataclass(frozen=True)
class AliasToolSpec:
    """表示插件清单中的 alias tool 定义。"""

    name: str  # str：alias 工具对外暴露的名称。
    target: str  # str：alias 实际转发到的目标工具名称。
    description: str = ''  # str：alias 工具的人类可读描述。
    arguments: JSONDict = field(default_factory=dict)  # JSONDict：调用目标工具时强制注入的参数。
    parameters: JSONDict = field(default_factory=dict)  # JSONDict：alias 工具对模型暴露的参数 schema。

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'AliasToolSpec':
        """从 JSON 字典恢复 alias 工具定义。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            AliasToolSpec: 恢复后的 alias 工具定义对象。
        Raises:
            ValueError: 当 alias 名称、目标工具或参数结构非法时抛出。
        """
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
    """表示插件清单中的 virtual tool 定义。"""

    name: str  # str：virtual 工具对外暴露的名称。
    description: str  # str：virtual 工具的人类可读描述。
    content: str  # str：virtual 工具返回给调用方的固定内容。
    parameters: JSONDict = field(default_factory=dict)  # JSONDict：virtual 工具对模型暴露的参数 schema。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict：virtual 工具调用时附带返回的固定元数据。

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'VirtualToolSpec':
        """从 JSON 字典恢复 virtual 工具定义。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            VirtualToolSpec: 恢复后的 virtual 工具定义对象。
        Raises:
            ValueError: 当 virtual 工具名称、描述、内容或参数结构非法时抛出。
        """
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
    """表示单个插件清单。"""

    name: str  # str：插件清单名称。
    summary: str = ''  # str：插件摘要说明。
    deny_tools: tuple[str, ...] = ()  # tuple[str, ...]：插件显式阻断的工具名称列表。
    deny_prefixes: tuple[str, ...] = ()  # tuple[str, ...]：插件按前缀阻断的工具名称前缀列表。
    before_hooks: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：工具调用前暴露的 hook 定义集合。
    after_hooks: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：工具调用后暴露的 hook 定义集合。
    aliases: tuple[AliasToolSpec, ...] = ()  # tuple[AliasToolSpec, ...]：插件声明的 alias 工具集合。
    virtual_tools: tuple[VirtualToolSpec, ...] = ()  # tuple[VirtualToolSpec, ...]：插件声明的 virtual 工具集合。
    source_path: Path | None = None  # Path | None：插件清单来源文件路径。

    @classmethod
    def from_dict(cls, payload: JSONDict | None, *, source_path: Path | None = None) -> 'PluginManifest':
        """从 JSON 字典恢复插件清单对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
            source_path (Path | None): 当前清单来源的文件路径。
        Returns:
            PluginManifest: 恢复后的插件清单对象。
        Raises:
            ValueError: 当插件名称、工具列表或工具定义非法时抛出。
        """
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
        """从磁盘文件加载并解析插件清单。

        Args:
            manifest_path (Path): 待加载的插件清单文件路径。
        Returns:
            PluginManifest: 解析成功后的插件清单对象。
        Raises:
            ValueError: 当文件内容不是合法插件对象时抛出。
            OSError: 当文件读取失败时抛出。
            json.JSONDecodeError: 当文件内容不是合法 JSON 时抛出。
        """
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Plugin manifest {manifest_path} must contain a JSON object')
        return cls.from_dict(payload, source_path=manifest_path)


@dataclass(frozen=True)
class PluginRegistration:
    """已注册的插件工具。"""

    tool_name: str  # str：最终注册到工具表中的名称。
    plugin_name: str  # str：提供该工具的插件名称。
    tool_kind: str  # str：工具来源类型，如 alias 或 virtual。
    target_name: str = ''  # str：alias 工具的目标工具名称；非 alias 时为空。


@dataclass(frozen=True)
class PluginConflict:
    """插件工具注册冲突。"""

    tool_name: str  # str：发生冲突的工具名称。
    plugin_name: str  # str：尝试注册该工具的插件名称。
    existing_source: str  # str：当前已占用该名称的来源说明。


@dataclass(frozen=True)
class PluginLoadError:
    """插件 manifest 加载或注册错误。"""

    plugin_name: str  # str：出错的插件名称或文件 stem。
    error: str  # str：对应的错误说明文本。
    source_path: Path | None = None  # Path | None：出错的来源文件路径。


@dataclass
class PluginCatalog:
    """表示工作区插件清单加载与注册后的运行时快照。

    典型工作流如下：
    1. 调用 `from_workspace()` 发现并加载全部插件清单。
    2. 在构建过程中注册 virtual/alias 工具，记录冲突和加载错误。
    3. 上层通过 `merge_tool_registry()`、`resolve_block()`、`get_before_hooks()` 等接口消费插件运行时结果。
    """

    manifests: tuple[PluginManifest, ...] = ()  # tuple[PluginManifest, ...]：已成功加载的插件清单集合。
    plugin_registry: dict[str, ToolDescriptor] = field(default_factory=dict)  # dict[str, ToolDescriptor]：插件新增工具的注册表。
    registrations: tuple[PluginRegistration, ...] = ()  # tuple[PluginRegistration, ...]：成功注册的插件工具记录。
    conflicts: tuple[PluginConflict, ...] = ()  # tuple[PluginConflict, ...]：注册阶段检测到的名称冲突记录。
    load_errors: tuple[PluginLoadError, ...] = ()  # tuple[PluginLoadError, ...]：加载或注册阶段收集到的错误信息。

    @classmethod
    def from_workspace(
        cls,
        workspace: Path,
        base_tool_registry: Mapping[str, ToolDescriptor],
    ) -> 'PluginCatalog':
        """从工作区加载插件清单并构建插件运行时。

        Args:
            workspace (Path): 工作区根目录。
            base_tool_registry (Mapping[str, ToolDescriptor]): 基础工具注册表。

        Returns:
            PluginCatalog: 含注册结果、冲突与错误信息的工作区插件目录对象。
        """
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
        plugin_registry: dict[str, ToolDescriptor] = {}
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

    def merge_tool_registry(self, base_tool_registry: Mapping[str, ToolDescriptor]) -> dict[str, ToolDescriptor]:
        """把插件工具注册表合并到基础工具注册表中。

        Args:
            base_tool_registry (Mapping[str, ToolDescriptor]): 当前基础工具注册表。
        Returns:
            dict[str, ToolDescriptor]: 合并后的完整工具注册表副本。
        """
        merged = dict(base_tool_registry)
        merged.update(self.plugin_registry)
        return merged

    def resolve_block(self, tool_name: str) -> JSONDict | None:
        """解析某个工具被插件规则阻断时的来源与原因。

        Args:
            tool_name (str): 需要解析阻断信息的工具名称。
        Returns:
            JSONDict | None: 命中插件阻断规则时返回结构化说明，否则返回 None。
        """
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
        """获取指定工具在执行前需要注入的插件 hooks。

        Args:
            tool_name (str): 当前即将执行的工具名称。
        Returns:
            tuple[JSONDict, ...]: 当前工具可见的 before hooks 集合。
        """
        return self._collect_hooks('before', tool_name)

    def get_after_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        """获取指定工具在执行后需要注入的插件 hooks。

        Args:
            tool_name (str): 当前已执行的工具名称。
        Returns:
            tuple[JSONDict, ...]: 当前工具可见的 after hooks 集合。
        """
        return self._collect_hooks('after', tool_name)

    def render_summary(self) -> str:
        """把插件加载与注册结果渲染为终端可读摘要。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            str: 包含插件、注册工具、冲突与错误信息的文本摘要；无内容时返回空字符串。
        """
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
        """按执行阶段收集插件提供的消息型 hooks。

        Args:
            phase (str): 当前 hook 阶段，只支持 `before` 或 `after`。
            tool_name (str): 当前关联的工具名称。
        Returns:
            tuple[JSONDict, ...]: 归一化后的 hook 载荷集合。
        """
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
    """发现工作区中所有候选插件清单文件路径。

    Args:
        workspace (Path): 工作区根目录。
    Returns:
        tuple[Path, ...]: 按稳定顺序返回的插件清单文件路径元组。
    """
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
    """规范化字符串列表输入。

    Args:
        value (object): 待规范化的原始值。
    Returns:
        tuple[str, ...]: 去空白后的字符串元组；非法输入返回空元组。
    """
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
    """规范化 hook 定义列表。

    Args:
        value (object): 待规范化的原始值。
    Returns:
        tuple[JSONDict, ...]: 仅保留字典项后的 hook 定义元组。
    """
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
    """校验插件声明的工具名称不存在重复。

    Args:
        plugin_name (str): 当前插件名称。
        aliases (tuple[AliasToolSpec, ...]): 当前插件声明的 alias 工具集合。
        virtual_tools (tuple[VirtualToolSpec, ...]): 当前插件声明的 virtual 工具集合。
    Returns:
        None: 校验通过时不返回值。
    Raises:
        ValueError: 当 alias 与 virtual 工具之间存在重名时抛出。
    """
    seen: set[str] = set()
    for tool_name in [item.name for item in virtual_tools] + [item.name for item in aliases]:
        if tool_name in seen:
            raise ValueError(f'Plugin manifest {plugin_name!r} declares duplicate tool {tool_name!r}')
        seen.add(tool_name)


def _build_alias_tool(manifest: PluginManifest, alias: AliasToolSpec, target_tool: ToolDescriptor) -> ToolDescriptor:
    """根据 alias 定义构造一个代理目标工具的 ToolDescriptor。

    Args:
        manifest (PluginManifest): 当前插件清单。
        alias (AliasToolSpec): 需要构造的 alias 工具定义。
        target_tool (ToolDescriptor): alias 最终转发到的目标工具。
    Returns:
        ToolDescriptor: 构造完成的 alias 工具对象。
    """
    def _handler(arguments: JSONDict, context: ToolExecutionContext):
        """在 alias 工具被调用时转发到目标工具。

        Args:
            arguments (JSONDict): 本次 alias 调用传入的参数。
            context (ToolExecutionContext): 当前工具执行上下文。
        Returns:
            Any: 保持目标工具原有返回结构，并补充插件来源元数据。
        """
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

    return ToolDescriptor(
        name=alias.name,
        description=alias.description or f'Alias for {alias.target} from plugin {manifest.name}.',
        parameters=alias.parameters or _derive_alias_parameters(target_tool.parameters, alias.arguments),
        handler=_handler,
    )


def _build_virtual_tool(manifest: PluginManifest, virtual_tool: VirtualToolSpec) -> ToolDescriptor:
    """根据 virtual 工具定义构造一个固定响应的 ToolDescriptor。

    Args:
        manifest (PluginManifest): 当前插件清单。
        virtual_tool (VirtualToolSpec): 需要构造的 virtual 工具定义。
    Returns:
        ToolDescriptor: 构造完成的 virtual 工具对象。
    """
    def _handler(arguments: JSONDict, context: ToolExecutionContext):
        """在 virtual 工具被调用时返回固定内容与插件元数据。

        Args:
            arguments (JSONDict): 本次 virtual 调用传入的参数；当前实现不消费该值。
            context (ToolExecutionContext): 当前工具执行上下文；当前实现不消费该值。
        Returns:
            Any: 固定内容和附加元数据组成的工具返回结构。
        """
        del arguments, context
        metadata = dict(virtual_tool.metadata)
        metadata.update(
            {
                'plugin_name': manifest.name,
                'plugin_tool_kind': 'virtual',
            }
        )
        return virtual_tool.content, metadata

    return ToolDescriptor(
        name=virtual_tool.name,
        description=virtual_tool.description,
        parameters=virtual_tool.parameters or dict(_EMPTY_OBJECT_SCHEMA),
        handler=_handler,
    )


def _derive_alias_parameters(target_parameters: JSONDict, forced_arguments: JSONDict) -> JSONDict:
    """从目标工具参数 schema 推导 alias 对外暴露的参数 schema。

    Args:
        target_parameters (JSONDict): 目标工具原始参数 schema。
        forced_arguments (JSONDict): alias 已经固定注入、无需再暴露给调用方的参数集合。
    Returns:
        JSONDict: 去掉固定参数后的 alias 参数 schema。
    """
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
    base_tool_registry: Mapping[str, ToolDescriptor],
    plugin_registry: Mapping[str, ToolDescriptor],
) -> str:
    """生成人类可读的工具来源说明文本。

    Args:
        tool_name (str): 需要描述来源的工具名称。
        base_tool_registry (Mapping[str, ToolDescriptor]): 基础工具注册表。
        plugin_registry (Mapping[str, ToolDescriptor]): 当前插件工具注册表。
    Returns:
        str: 面向冲突诊断的人类可读来源说明。
    """
    if tool_name in base_tool_registry:
        return f'core tool {tool_name}'
    if tool_name in plugin_registry:
        return f'plugin tool {tool_name}'
    return f'existing tool {tool_name}'


