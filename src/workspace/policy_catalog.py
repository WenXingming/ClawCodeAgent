"""管理工作区 hook policy 清单的发现、合并与运行时暴露。

本模块负责从工作区发现策略清单，过滤不可信 manifest，合并工具阻断、环境变量、预算覆盖和 before/after hooks，并把合并结果暴露为运行时可直接消费的策略快照。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core_contracts.budget import BudgetConfig
from core_contracts.protocol import JSONDict
from core_contracts.tools_contracts import ToolDescriptor


_POLICY_MANIFEST_FILE = Path('.claw') / 'policies.json'
_POLICY_MANIFEST_DIR = Path('.claw') / 'policies'


@dataclass(frozen=True)
class HookPolicyManifest:
    """表示单个 hook policy 清单。

    该对象是策略文件在内存中的稳定表示，描述工具禁用规则、安全环境变量、预算覆盖和消息型 hooks 等策略配置。
    """

    name: str  # str：策略清单的展示名称。
    trusted: bool = True  # bool：当前清单是否被视为可信并允许参与合并。
    deny_tools: tuple[str, ...] = ()  # tuple[str, ...]：被显式禁止的工具名称列表。
    deny_prefixes: tuple[str, ...] = ()  # tuple[str, ...]：被按前缀禁止的工具名称前缀列表。
    safe_env: dict[str, str] = field(default_factory=dict)  # dict[str, str]：允许注入运行时的安全环境变量映射。
    budget_overrides: BudgetConfig = field(default_factory=BudgetConfig)  # BudgetConfig：当前清单提供的预算覆盖配置。
    before_hooks: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：在工具调用前暴露的 hook 定义集合。
    after_hooks: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：在工具调用后暴露的 hook 定义集合。
    source_path: Path | None = None  # Path | None：当前清单文件的来源路径。

    @classmethod
    def from_dict(
        cls,
        payload: JSONDict | None,
        *,
        source_path: Path | None = None,
    ) -> 'HookPolicyManifest':
        """从 JSON 字典恢复单个策略清单对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
            source_path (Path | None): 当前清单来源的文件路径。
        Returns:
            HookPolicyManifest: 恢复后的策略清单对象。
        Raises:
            ValueError: 当清单名称为空或字段类型不符合预期时抛出。
        """
        data = dict(payload or {})
        name = str(data.get('name', '')).strip()
        if not name:
            raise ValueError('Hook policy manifest requires non-empty name')

        deny_tools = _normalize_string_list(data.get('deny_tools', data.get('denyTools', [])))
        deny_prefixes = _normalize_string_list(data.get('deny_prefixes', data.get('denyPrefixes', [])))
        safe_env = _normalize_string_mapping(data.get('safe_env', data.get('safeEnv', {})))
        before_hooks = _normalize_hook_list(data.get('before_hooks', data.get('beforeHooks', [])))
        after_hooks = _normalize_hook_list(data.get('after_hooks', data.get('afterHooks', [])))

        return cls(
            name=name,
            trusted=bool(data.get('trusted', True)),
            deny_tools=deny_tools,
            deny_prefixes=deny_prefixes,
            safe_env=safe_env,
            budget_overrides=BudgetConfig.from_dict(
                data.get('budget_overrides', data.get('budgetOverrides', {}))
            ),
            before_hooks=before_hooks,
            after_hooks=after_hooks,
            source_path=source_path.resolve() if source_path else None,
        )

    @classmethod
    def from_path(cls, manifest_path: Path) -> 'HookPolicyManifest':
        """从磁盘文件加载并解析策略清单。

        Args:
            manifest_path (Path): 待加载的策略清单文件路径。
        Returns:
            HookPolicyManifest: 解析成功后的策略清单对象。
        Raises:
            ValueError: 当文件内容不是合法的策略对象时抛出。
            OSError: 当文件读取失败时抛出。
            json.JSONDecodeError: 当文件内容不是合法 JSON 时抛出。
        """
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Hook policy manifest {manifest_path} must contain a JSON object')
        return cls.from_dict(payload, source_path=manifest_path)


@dataclass(frozen=True)
class HookPolicyLoadError:
    """表示策略清单加载失败时的错误信息。"""

    name: str  # str：加载失败的策略名称或文件 stem。
    error: str  # str：对应的错误说明文本。
    source_path: Path | None = None  # Path | None：出错的来源文件路径。


@dataclass
class PolicyCatalog:
    """表示工作区合并后的 hook policy 运行时快照。

    典型工作流如下：
    1. 调用 `from_workspace()` 从工作区发现并合并可信策略清单。
    2. 通过 `is_tool_denied()`、`filter_tool_registry()`、`resolve_block()` 查询工具阻断结果。
    3. 通过 `get_before_hooks()` 和 `get_after_hooks()` 在工具执行前后暴露消息型 hooks。
    """

    manifests: tuple[HookPolicyManifest, ...] = ()  # tuple[HookPolicyManifest, ...]：已加载并参与合并的可信策略清单集合。
    skipped_manifests: tuple[HookPolicyManifest, ...] = ()  # tuple[HookPolicyManifest, ...]：因不可信而被跳过的策略清单集合。
    load_errors: tuple[HookPolicyLoadError, ...] = ()  # tuple[HookPolicyLoadError, ...]：加载阶段收集到的错误信息。
    deny_tools: tuple[str, ...] = ()  # tuple[str, ...]：合并后的显式禁用工具列表。
    deny_prefixes: tuple[str, ...] = ()  # tuple[str, ...]：合并后的工具前缀禁用规则。
    before_hooks: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：合并后的 before hook 定义集合。
    after_hooks: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：合并后的 after hook 定义集合。
    safe_env: dict[str, str] = field(default_factory=dict)  # dict[str, str]：合并后的安全环境变量映射。
    budget_overrides: BudgetConfig = field(default_factory=BudgetConfig)  # BudgetConfig：合并后的预算覆盖配置。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'PolicyCatalog':
        """从工作区发现并加载 hook policy 清单。

        Args:
            workspace (Path): 工作区根目录。

        Returns:
            PolicyCatalog: 合并策略与加载错误后的工作区策略目录对象。
        """
        manifests: list[HookPolicyManifest] = []
        skipped_manifests: list[HookPolicyManifest] = []
        load_errors: list[HookPolicyLoadError] = []

        for manifest_path in _discover_manifest_paths(workspace.resolve()):
            try:
                manifest = HookPolicyManifest.from_path(manifest_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                load_errors.append(
                    HookPolicyLoadError(
                        name=manifest_path.stem,
                        error=str(exc),
                        source_path=manifest_path,
                    )
                )
                continue

            if not manifest.trusted:
                skipped_manifests.append(manifest)
                continue
            manifests.append(manifest)

        merged_deny_tools: list[str] = []
        merged_deny_prefixes: list[str] = []
        merged_before_hooks: list[JSONDict] = []
        merged_after_hooks: list[JSONDict] = []
        merged_safe_env: dict[str, str] = {}
        merged_budget = BudgetConfig()

        for manifest in manifests:
            _extend_unique(merged_deny_tools, manifest.deny_tools)
            _extend_unique(merged_deny_prefixes, manifest.deny_prefixes)
            merged_before_hooks.extend(dict(item) for item in manifest.before_hooks)
            merged_after_hooks.extend(dict(item) for item in manifest.after_hooks)
            merged_safe_env.update(manifest.safe_env)
            merged_budget = _merge_budget_configs(merged_budget, manifest.budget_overrides)

        return cls(
            manifests=tuple(manifests),
            skipped_manifests=tuple(skipped_manifests),
            load_errors=tuple(load_errors),
            deny_tools=tuple(merged_deny_tools),
            deny_prefixes=tuple(merged_deny_prefixes),
            before_hooks=tuple(merged_before_hooks),
            after_hooks=tuple(merged_after_hooks),
            safe_env=merged_safe_env,
            budget_overrides=merged_budget,
        )

    def is_tool_denied(self, tool_name: str) -> bool:
        """判断某个工具是否被当前策略显式阻断。

        Args:
            tool_name (str): 需要检查的工具名称。
        Returns:
            bool: 工具命中显式名称或前缀阻断规则时返回 True，否则返回 False。
        """
        if tool_name in self.deny_tools:
            return True
        return any(tool_name.startswith(prefix) for prefix in self.deny_prefixes)

    def filter_tool_registry(
        self,
        tool_registry: dict[str, ToolDescriptor],
    ) -> dict[str, ToolDescriptor]:
        """从工具注册表中过滤掉被策略阻断的工具。

        Args:
            tool_registry (dict[str, ToolDescriptor]): 当前可用工具注册表。
        Returns:
            dict[str, ToolDescriptor]: 过滤后的工具注册表副本。
        """
        return {
            name: tool
            for name, tool in tool_registry.items()
            if not self.is_tool_denied(name)
        }

    def apply_budget_config(self, budget_config: BudgetConfig) -> BudgetConfig:
        """把策略中的预算覆盖应用到基础预算配置。"""
        return _merge_budget_configs(budget_config, self.budget_overrides)

    def resolve_block(self, tool_name: str) -> JSONDict | None:
        """解析某个工具被阻断时的来源与原因。

        Args:
            tool_name (str): 需要解析阻断信息的工具名称。
        Returns:
            JSONDict | None: 命中阻断规则时返回包含来源、原因和消息的结构化说明，否则返回 None。
        """
        if not self.manifests:
            if tool_name in self.deny_tools:
                return {
                    'source': 'policy',
                    'source_name': 'merged-policy',
                    'reason': 'deny_tools',
                    'message': f'Tool {tool_name} blocked by policy merged-policy.',
                }
            for prefix in self.deny_prefixes:
                if tool_name.startswith(prefix):
                    return {
                        'source': 'policy',
                        'source_name': 'merged-policy',
                        'reason': 'deny_prefixes',
                        'message': f'Tool {tool_name} blocked by policy merged-policy.',
                    }
            return None
        for manifest in self.manifests:
            if tool_name in manifest.deny_tools:
                return {
                    'source': 'policy',
                    'source_name': manifest.name,
                    'reason': 'deny_tools',
                    'message': f'Tool {tool_name} blocked by policy {manifest.name}.',
                }
            for prefix in manifest.deny_prefixes:
                if tool_name.startswith(prefix):
                    return {
                        'source': 'policy',
                        'source_name': manifest.name,
                        'reason': 'deny_prefixes',
                        'message': f'Tool {tool_name} blocked by policy {manifest.name}.',
                    }
        return None

    def get_before_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        """获取指定工具在执行前需要注入的 hooks。

        Args:
            tool_name (str): 当前即将执行的工具名称。
        Returns:
            tuple[JSONDict, ...]: 当前工具对应的 before hooks 集合。
        """
        return self._collect_hooks('before', tool_name)

    def get_after_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        """获取指定工具在执行后需要注入的 hooks。

        Args:
            tool_name (str): 当前已执行的工具名称。
        Returns:
            tuple[JSONDict, ...]: 当前工具对应的 after hooks 集合。
        """
        return self._collect_hooks('after', tool_name)

    def render_summary(self) -> str:
        """把当前策略快照渲染为便于终端显示的摘要文本。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            str: 包含已加载、跳过和加载失败策略信息的文本摘要；无内容时返回空字符串。
        """
        if not self.manifests and not self.skipped_manifests and not self.load_errors:
            return ''

        lines = ['Hook Policies', '=============']
        for manifest in self.manifests:
            lines.append(manifest.name)
        if self.skipped_manifests:
            lines.extend(['', 'Skipped', '-------'])
            for manifest in self.skipped_manifests:
                lines.append(f'{manifest.name} (untrusted)')
        if self.load_errors:
            lines.extend(['', 'Load Errors', '-----------'])
            for item in self.load_errors:
                location = f' ({item.source_path})' if item.source_path else ''
                lines.append(f'{item.name}{location}: {item.error}')
        return '\n'.join(lines)

    def _collect_hooks(self, phase: str, tool_name: str) -> tuple[JSONDict, ...]:
        """按执行阶段收集可用的消息型 hooks。

        Args:
            phase (str): 当前 hook 阶段，只支持 `before` 或 `after`。
            tool_name (str): 当前关联的工具名称。
        Returns:
            tuple[JSONDict, ...]: 归一化后的 hook 载荷集合。
        """
        if not self.manifests:
            raw_hooks = self.before_hooks if phase == 'before' else self.after_hooks
            hooks: list[JSONDict] = []
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
                        'source': 'policy',
                        'source_name': 'merged-policy',
                    }
                )
            return tuple(hooks)
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
                        'source': 'policy',
                        'source_name': manifest.name,
                    }
                )
        return tuple(hooks)


def _discover_manifest_paths(workspace: Path) -> tuple[Path, ...]:
    """发现工作区中所有候选策略清单文件路径。

    Args:
        workspace (Path): 工作区根目录。
    Returns:
        tuple[Path, ...]: 按稳定顺序返回的策略清单文件路径元组。
    """
    candidates: list[Path] = []
    single_manifest = workspace / _POLICY_MANIFEST_FILE
    if single_manifest.is_file():
        candidates.append(single_manifest.resolve())

    manifest_dir = workspace / _POLICY_MANIFEST_DIR
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


def _normalize_string_mapping(value: object) -> dict[str, str]:
    """规范化字符串键值映射输入。

    Args:
        value (object): 待规范化的原始值。
    Returns:
        dict[str, str]: 过滤非法键后的字符串映射；非法输入返回空字典。
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        normalized_key = key.strip()
        if not normalized_key:
            continue
        result[normalized_key] = str(item)
    return result


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


def _extend_unique(target: list[str], values: tuple[str, ...]) -> None:
    """把值集合按唯一性追加到目标列表。

    Args:
        target (list[str]): 需要原地追加的目标列表。
        values (tuple[str, ...]): 待追加的值集合。
    Returns:
        None: 该函数原地修改目标列表。
    """
    for item in values:
        if item not in target:
            target.append(item)


def _merge_budget_configs(base: BudgetConfig, override: BudgetConfig) -> BudgetConfig:
    """把预算覆盖配置合并到基础预算配置中。

    Args:
        base (BudgetConfig): 当前基础预算配置。
        override (BudgetConfig): 需要覆盖到基础配置上的预算配置。
    Returns:
        BudgetConfig: 合并后的预算配置对象。
    """
    payload = base.to_dict()
    for key, value in override.to_dict().items():
        if value is not None:
            payload[key] = value
    return BudgetConfig.from_dict(payload)

