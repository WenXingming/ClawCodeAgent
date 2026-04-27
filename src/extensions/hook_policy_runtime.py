"""ISSUE-015 Hook Policy Runtime：policy manifest 发现、合并与运行时暴露。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from core_contracts.config import AgentRuntimeConfig, BudgetConfig
from core_contracts.protocol import JSONDict
from tools.local_tools import LocalTool


_POLICY_MANIFEST_FILE = Path('.claw') / 'policies.json'
_POLICY_MANIFEST_DIR = Path('.claw') / 'policies'


@dataclass(frozen=True)
class HookPolicyManifest:
    """单个 policy manifest。"""

    name: str
    trusted: bool = True
    deny_tools: tuple[str, ...] = ()
    deny_prefixes: tuple[str, ...] = ()
    safe_env: dict[str, str] = field(default_factory=dict)
    budget_overrides: BudgetConfig = field(default_factory=BudgetConfig)
    before_hooks: tuple[JSONDict, ...] = ()
    after_hooks: tuple[JSONDict, ...] = ()
    source_path: Path | None = None

    @classmethod
    def from_dict(
        cls,
        payload: JSONDict | None,
        *,
        source_path: Path | None = None,
    ) -> 'HookPolicyManifest':
        """执行 `from_dict` 逻辑。
        Args:
            payload (JSONDict | None): 参数 `payload`。
            source_path (Path | None): 参数 `source_path`。
        Returns:
            'HookPolicyManifest': 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
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
        """执行 `from_path` 逻辑。
        Args:
            manifest_path (Path): 参数 `manifest_path`。
        Returns:
            'HookPolicyManifest': 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Hook policy manifest {manifest_path} must contain a JSON object')
        return cls.from_dict(payload, source_path=manifest_path)


@dataclass(frozen=True)
class HookPolicyLoadError:
    """policy manifest 加载错误。"""

    name: str
    error: str
    source_path: Path | None = None


@dataclass
class HookPolicyRuntime:
    """工作区 hook/policy 运行时快照。"""

    manifests: tuple[HookPolicyManifest, ...] = ()
    skipped_manifests: tuple[HookPolicyManifest, ...] = ()
    load_errors: tuple[HookPolicyLoadError, ...] = ()
    deny_tools: tuple[str, ...] = ()
    deny_prefixes: tuple[str, ...] = ()
    before_hooks: tuple[JSONDict, ...] = ()
    after_hooks: tuple[JSONDict, ...] = ()
    safe_env: dict[str, str] = field(default_factory=dict)
    budget_overrides: BudgetConfig = field(default_factory=BudgetConfig)

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'HookPolicyRuntime':
        """从工作区发现并加载 hook policy 清单。

        Args:
            workspace (Path): 工作区根目录。

        Returns:
            HookPolicyRuntime: 合并策略与加载错误后的运行时快照。
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
        """执行 `is_tool_denied` 逻辑。
        Args:
            tool_name (str): 参数 `tool_name`。
        Returns:
            bool: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        if tool_name in self.deny_tools:
            return True
        return any(tool_name.startswith(prefix) for prefix in self.deny_prefixes)

    def filter_tool_registry(
        self,
        tool_registry: dict[str, LocalTool],
    ) -> dict[str, LocalTool]:
        """执行 `filter_tool_registry` 逻辑。
        Args:
            tool_registry (dict[str, LocalTool]): 参数 `tool_registry`。
        Returns:
            dict[str, LocalTool]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        return {
            name: tool
            for name, tool in tool_registry.items()
            if not self.is_tool_denied(name)
        }

    def apply_runtime_config(self, runtime_config: AgentRuntimeConfig) -> AgentRuntimeConfig:
        """执行 `apply_runtime_config` 逻辑。
        Args:
            runtime_config (AgentRuntimeConfig): 参数 `runtime_config`。
        Returns:
            AgentRuntimeConfig: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        merged_budget = _merge_budget_configs(runtime_config.budget_config, self.budget_overrides)
        if merged_budget == runtime_config.budget_config:
            return runtime_config
        return replace(runtime_config, budget_config=merged_budget)

    def resolve_block(self, tool_name: str) -> JSONDict | None:
        """执行 `resolve_block` 逻辑。
        Args:
            tool_name (str): 参数 `tool_name`。
        Returns:
            JSONDict | None: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
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
        """执行 `get_before_hooks` 逻辑。
        Args:
            tool_name (str): 参数 `tool_name`。
        Returns:
            tuple[JSONDict, ...]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        return self._collect_hooks('before', tool_name)

    def get_after_hooks(self, tool_name: str) -> tuple[JSONDict, ...]:
        """执行 `get_after_hooks` 逻辑。
        Args:
            tool_name (str): 参数 `tool_name`。
        Returns:
            tuple[JSONDict, ...]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        return self._collect_hooks('after', tool_name)

    def render_summary(self) -> str:
        """执行 `render_summary` 逻辑。
        Args:
            None: 无参数。
        Returns:
            str: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
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
        """内部方法：执行 `_collect_hooks` 相关逻辑。
        Args:
            phase (str): 参数 `phase`。
            tool_name (str): 参数 `tool_name`。
        Returns:
            tuple[JSONDict, ...]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
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
    """内部方法：执行 `_discover_manifest_paths` 相关逻辑。
    Args:
        workspace (Path): 参数 `workspace`。
    Returns:
        tuple[Path, ...]: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
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
    """内部方法：执行 `_normalize_string_list` 相关逻辑。
    Args:
        value (object): 参数 `value`。
    Returns:
        tuple[str, ...]: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
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
    """内部方法：执行 `_normalize_string_mapping` 相关逻辑。
    Args:
        value (object): 参数 `value`。
    Returns:
        dict[str, str]: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
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
    """内部方法：执行 `_normalize_hook_list` 相关逻辑。
    Args:
        value (object): 参数 `value`。
    Returns:
        tuple[JSONDict, ...]: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    if not isinstance(value, list):
        return ()
    hooks: list[JSONDict] = []
    for item in value:
        if isinstance(item, dict):
            hooks.append(dict(item))
    return tuple(hooks)


def _extend_unique(target: list[str], values: tuple[str, ...]) -> None:
    """内部方法：执行 `_extend_unique` 相关逻辑。
    Args:
        target (list[str]): 参数 `target`。
        values (tuple[str, ...]): 参数 `values`。
    Returns:
        None: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    for item in values:
        if item not in target:
            target.append(item)


def _merge_budget_configs(base: BudgetConfig, override: BudgetConfig) -> BudgetConfig:
    """内部方法：执行 `_merge_budget_configs` 相关逻辑。
    Args:
        base (BudgetConfig): 参数 `base`。
        override (BudgetConfig): 参数 `override`。
    Returns:
        BudgetConfig: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    payload = base.to_dict()
    for key, value in override.to_dict().items():
        if value is not None:
            payload[key] = value
    return BudgetConfig.from_dict(payload)

