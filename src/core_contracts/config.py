"""配置对象相关契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._coerce import (
    _as_bool,
    _as_dict,
    _as_float,
    _as_int,
    _as_optional_float,
    _as_optional_int,
    _as_str,
    _first_present,
    _path_or_default,
)
from .protocol import JSONDict
from .usage import ModelPricing


@dataclass(frozen=True)
class BudgetConfig:
    """运行期预算限制，用于保证安全和可预测性。"""

    max_total_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_reasoning_tokens: int | None = None
    max_total_cost_usd: float | None = None
    max_tool_calls: int | None = None
    max_delegated_tasks: int | None = None
    max_model_calls: int | None = None
    max_session_turns: int | None = None

    def to_dict(self) -> JSONDict:
        return {
            'max_total_tokens': self.max_total_tokens,
            'max_input_tokens': self.max_input_tokens,
            'max_output_tokens': self.max_output_tokens,
            'max_reasoning_tokens': self.max_reasoning_tokens,
            'max_total_cost_usd': self.max_total_cost_usd,
            'max_tool_calls': self.max_tool_calls,
            'max_delegated_tasks': self.max_delegated_tasks,
            'max_model_calls': self.max_model_calls,
            'max_session_turns': self.max_session_turns,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'BudgetConfig':
        data = _as_dict(payload)
        return cls(
            max_total_tokens=_as_optional_int(
                _first_present(data, 'max_total_tokens', 'maxTotalTokens')
            ),
            max_input_tokens=_as_optional_int(
                _first_present(data, 'max_input_tokens', 'maxInputTokens')
            ),
            max_output_tokens=_as_optional_int(
                _first_present(data, 'max_output_tokens', 'maxOutputTokens')
            ),
            max_reasoning_tokens=_as_optional_int(
                _first_present(data, 'max_reasoning_tokens', 'maxReasoningTokens')
            ),
            max_total_cost_usd=_as_optional_float(
                _first_present(data, 'max_total_cost_usd', 'maxTotalCostUsd')
            ),
            max_tool_calls=_as_optional_int(
                _first_present(data, 'max_tool_calls', 'maxToolCalls')
            ),
            max_delegated_tasks=_as_optional_int(
                _first_present(data, 'max_delegated_tasks', 'maxDelegatedTasks')
            ),
            max_model_calls=_as_optional_int(
                _first_present(data, 'max_model_calls', 'maxModelCalls')
            ),
            max_session_turns=_as_optional_int(
                _first_present(data, 'max_session_turns', 'maxSessionTurns')
            ),
        )


@dataclass(frozen=True)
class OutputSchemaConfig:
    """可选的结构化输出 schema 配置。"""

    name: str
    schema: JSONDict
    strict: bool = False

    def to_dict(self) -> JSONDict:
        return {
            'name': self.name,
            'schema': dict(self.schema),
            'strict': self.strict,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'OutputSchemaConfig | None':
        data = _as_dict(payload)
        name = _as_str(data.get('name'), '').strip()
        schema = data.get('schema')
        if not name or not isinstance(schema, dict):
            return None
        return cls(
            name=name,
            schema=dict(schema),
            strict=_as_bool(data.get('strict'), False),
        )


@dataclass(frozen=True)
class ModelConfig:
    """OpenAI-compatible 客户端使用的模型后端配置。"""

    model: str
    base_url: str = 'http://127.0.0.1:8000/v1'
    api_key: str = 'local-token'
    temperature: float = 0.0
    timeout_seconds: float = 120.0
    pricing: ModelPricing = field(default_factory=ModelPricing)

    def to_dict(self) -> JSONDict:
        return {
            'model': self.model,
            'base_url': self.base_url,
            'api_key': self.api_key,
            'temperature': self.temperature,
            'timeout_seconds': self.timeout_seconds,
            'pricing': self.pricing.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ModelConfig':
        data = _as_dict(payload)
        model = _as_str(data.get('model'), '').strip() or 'unknown-model'
        return cls(
            model=model,
            base_url=_as_str(
                _first_present(data, 'base_url', 'baseUrl', default='http://127.0.0.1:8000/v1'),
                'http://127.0.0.1:8000/v1',
            ),
            api_key=_as_str(_first_present(data, 'api_key', 'apiKey', default='local-token'), 'local-token'),
            temperature=_as_float(data.get('temperature'), 0.0),
            timeout_seconds=_as_float(
                _first_present(data, 'timeout_seconds', 'timeoutSeconds', default=120.0),
                120.0,
            ),
            pricing=ModelPricing.from_dict(data.get('pricing')),
        )


@dataclass(frozen=True)
class AgentPermissions:
    """运行时和工具执行使用的权限开关。"""

    allow_file_write: bool = False
    allow_shell_commands: bool = False
    allow_destructive_shell_commands: bool = False

    def to_dict(self) -> JSONDict:
        return {
            'allow_file_write': self.allow_file_write,
            'allow_shell_commands': self.allow_shell_commands,
            'allow_destructive_shell_commands': self.allow_destructive_shell_commands,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'AgentPermissions':
        data = _as_dict(payload)
        return cls(
            allow_file_write=_as_bool(
                _first_present(data, 'allow_file_write', 'allowFileWrite'),
                False,
            ),
            allow_shell_commands=_as_bool(
                _first_present(data, 'allow_shell_commands', 'allowShellCommands'),
                False,
            ),
            allow_destructive_shell_commands=_as_bool(
                _first_present(
                    data,
                    'allow_destructive_shell_commands',
                    'allowDestructiveShellCommands',
                ),
                False,
            ),
        )


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """运行配置：执行选项与工作目录路径。"""

    cwd: Path
    max_turns: int = 12
    command_timeout_seconds: float = 30.0
    max_output_chars: int = 12000
    stream_model_responses: bool = False
    auto_snip_threshold_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    compact_preserve_messages: int = 4
    permissions: AgentPermissions = field(default_factory=AgentPermissions)
    additional_working_directories: tuple[Path, ...] = ()
    disable_claude_md_discovery: bool = False
    budget_config: BudgetConfig = field(default_factory=BudgetConfig)
    output_schema: OutputSchemaConfig | None = None
    session_directory: Path = field(default_factory=lambda: (Path('.port_sessions') / 'agent').resolve())
    scratchpad_root: Path = field(default_factory=lambda: (Path('.port_sessions') / 'scratchpad').resolve())

    def to_dict(self) -> JSONDict:
        return {
            'cwd': str(self.cwd),
            'max_turns': self.max_turns,
            'command_timeout_seconds': self.command_timeout_seconds,
            'max_output_chars': self.max_output_chars,
            'stream_model_responses': self.stream_model_responses,
            'auto_snip_threshold_tokens': self.auto_snip_threshold_tokens,
            'auto_compact_threshold_tokens': self.auto_compact_threshold_tokens,
            'compact_preserve_messages': self.compact_preserve_messages,
            'permissions': self.permissions.to_dict(),
            'additional_working_directories': [str(path) for path in self.additional_working_directories],
            'disable_claude_md_discovery': self.disable_claude_md_discovery,
            'budget_config': self.budget_config.to_dict(),
            'output_schema': self.output_schema.to_dict() if self.output_schema else None,
            'session_directory': str(self.session_directory),
            'scratchpad_root': str(self.scratchpad_root),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'AgentRuntimeConfig':
        data = _as_dict(payload)
        default_session_dir = (Path('.port_sessions') / 'agent').resolve()
        default_scratchpad_root = (Path('.port_sessions') / 'scratchpad').resolve()
        additional_dirs_raw = data.get(
            'additional_working_directories', data.get('additionalWorkingDirectories', [])
        )
        if not isinstance(additional_dirs_raw, list):
            additional_dirs_raw = []

        return cls(
            cwd=_path_or_default(data.get('cwd'), Path('.').resolve()),
            max_turns=_as_int(_first_present(data, 'max_turns', 'maxTurns', default=12), 12),
            command_timeout_seconds=_as_float(
                _first_present(
                    data,
                    'command_timeout_seconds',
                    'commandTimeoutSeconds',
                    default=30.0,
                ),
                30.0,
            ),
            max_output_chars=_as_int(
                _first_present(data, 'max_output_chars', 'maxOutputChars', default=12000),
                12000,
            ),
            stream_model_responses=_as_bool(
                _first_present(data, 'stream_model_responses', 'streamModelResponses'),
                False,
            ),
            auto_snip_threshold_tokens=_as_optional_int(
                _first_present(
                    data,
                    'auto_snip_threshold_tokens',
                    'autoSnipThresholdTokens',
                )
            ),
            auto_compact_threshold_tokens=_as_optional_int(
                _first_present(
                    data,
                    'auto_compact_threshold_tokens',
                    'autoCompactThresholdTokens',
                )
            ),
            compact_preserve_messages=_as_int(
                _first_present(
                    data,
                    'compact_preserve_messages',
                    'compactPreserveMessages',
                    default=4,
                ),
                4,
            ),
            permissions=AgentPermissions.from_dict(data.get('permissions')),
            additional_working_directories=tuple(
                Path(str(item)).resolve()
                for item in additional_dirs_raw
                if isinstance(item, str) and item.strip()
            ),
            disable_claude_md_discovery=_as_bool(
                _first_present(
                    data,
                    'disable_claude_md_discovery',
                    'disableClaudeMdDiscovery',
                ),
                False,
            ),
            budget_config=BudgetConfig.from_dict(
                _first_present(data, 'budget_config', 'budgetConfig', default={})
            ),
            output_schema=OutputSchemaConfig.from_dict(
                _first_present(data, 'output_schema', 'outputSchema', default=None)
            ),
            session_directory=_path_or_default(data.get('session_directory'), default_session_dir),
            scratchpad_root=_path_or_default(data.get('scratchpad_root'), default_scratchpad_root),
        )