"""ISSUE-001 的核心契约模型。

这个模块刻意把核心数据契约集中在一个文件中，
便于早期开发阶段阅读、理解和扩展。

设计目标：
1) 使用简单的 dataclass 模型。
2) from_dict 对不完整或异常数据保持安全容错。
3) 同时兼容 snake_case、camelCase 以及常见后端别名字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 通用类型与解析辅助函数
# ---------------------------------------------------------------------------

# 统一 JSON 对象类型，便于后续模块复用。
JSONDict = dict[str, Any]


def _first_present(data: JSONDict, *keys: str, default: Any = None) -> Any:
    """按顺序返回第一个存在且非 None 的字段值。"""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_int(value: Any, default: int = 0) -> int:
    """安全地将值转换为 int，遇到异常或不适合的类型时返回默认值。"""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> int | None:
    """将值转换为 int 或 None，遇到异常或不适合的类型时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    """安全地将值转换为 float，遇到异常或不适合的类型时返回默认值。"""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: Any) -> float | None:
    """将值转换为 float 或 None，遇到异常或不适合的类型时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any, default: bool = False) -> bool:
    """将值转换为 bool，支持多种常见表示形式。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', '1', 'yes', 'on'}:
            return True
        if lowered in {'false', '0', 'no', 'off'}:
            return False
    return default


def _as_str(value: Any, default: str = '') -> str:
    """将值转换为 str，遇到异常或不适合的类型时返回默认值。"""
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_dict(value: Any) -> JSONDict:
    """将值转换为 dict，遇到异常或不适合的类型时返回空 dict。"""
    if isinstance(value, dict):
        return dict(value)
    return {}


def _path_or_default(value: Any, default: Path) -> Path:
    """将值转换为 Path 并解析为绝对路径，遇到异常或不适合的类型时返回默认路径。
       如果传入的是一个绝对路径字符串，则直接解析；
       如果是一个相对路径字符串，则相对于当前工作目录解析（Path 的默认行为）；
       如果是 None、空字符串或其他非字符串类型，则返回 default.resolve()。
    """
    text = _as_str(value, '')
    if not text:
        return default.resolve()
    return Path(text).resolve()


# ---------------------------------------------------------------------------
# 契约模型定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """模型调用产生的 token 使用统计。"""

    input_tokens: int = 0  # 输入 tokens（含 prompt 与工具参数）。
    output_tokens: int = 0  # 输出 tokens（含 completion）。
    cache_creation_input_tokens: int = 0  # 创建缓存写入的输入 tokens。
    cache_read_input_tokens: int = 0  # 命中缓存读取的输入 tokens。
    reasoning_tokens: int = 0  # 推理阶段产生的 tokens。

    @property
    def total_tokens(self) -> int:
        """返回输入、输出与缓存相关 tokens 的总和。"""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: 'TokenUsage') -> 'TokenUsage':
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_dict(self) -> JSONDict:
        return {
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cache_creation_input_tokens': self.cache_creation_input_tokens,
            'cache_read_input_tokens': self.cache_read_input_tokens,
            'reasoning_tokens': self.reasoning_tokens,
            'total_tokens': self.total_tokens,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'TokenUsage':
        data = _as_dict(payload)
        # 兼容 OpenAI 生态中常见的替代字段命名。
        return cls(
            input_tokens=_as_int(
                _first_present(data, 'input_tokens', 'prompt_tokens', 'inputTokens', default=0),
                0,
            ),
            output_tokens=_as_int(
                _first_present(
                    data,
                    'output_tokens',
                    'completion_tokens',
                    'outputTokens',
                    default=0,
                ),
                0,
            ),
            cache_creation_input_tokens=_as_int(
                _first_present(
                    data,
                    'cache_creation_input_tokens',
                    'cacheCreationInputTokens',
                    default=0,
                ),
                0,
            ),
            cache_read_input_tokens=_as_int(
                _first_present(
                    data,
                    'cache_read_input_tokens',
                    'cacheReadInputTokens',
                    default=0,
                ),
                0,
            ),
            reasoning_tokens=_as_int(
                _first_present(data, 'reasoning_tokens', 'reasoningTokens', default=0),
                0,
            ),
        )


@dataclass(frozen=True)
class ModelPricing:
    """用于估算会话成本的计费配置。"""

    input_cost_per_million_tokens_usd: float = 0.0  # 每百万输入 tokens 单价（USD）。
    output_cost_per_million_tokens_usd: float = 0.0  # 每百万输出 tokens 单价（USD）。
    cache_creation_input_cost_per_million_tokens_usd: float = 0.0  # 每百万缓存写入输入 tokens 单价（USD）。
    cache_read_input_cost_per_million_tokens_usd: float = 0.0  # 每百万缓存读取输入 tokens 单价（USD）。

    def estimate_cost_usd(self, usage: TokenUsage) -> float:
        return (
            (usage.input_tokens / 1_000_000.0) * self.input_cost_per_million_tokens_usd
            + (usage.output_tokens / 1_000_000.0) * self.output_cost_per_million_tokens_usd
            + (
                (usage.cache_creation_input_tokens / 1_000_000.0)
                * self.cache_creation_input_cost_per_million_tokens_usd
            )
            + (
                (usage.cache_read_input_tokens / 1_000_000.0)
                * self.cache_read_input_cost_per_million_tokens_usd
            )
        )

    def to_dict(self) -> JSONDict:
        return {
            'input_cost_per_million_tokens_usd': self.input_cost_per_million_tokens_usd,
            'output_cost_per_million_tokens_usd': self.output_cost_per_million_tokens_usd,
            'cache_creation_input_cost_per_million_tokens_usd': (
                self.cache_creation_input_cost_per_million_tokens_usd
            ),
            'cache_read_input_cost_per_million_tokens_usd': (
                self.cache_read_input_cost_per_million_tokens_usd
            ),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ModelPricing':
        data = _as_dict(payload)
        return cls(
            input_cost_per_million_tokens_usd=_as_float(
                _first_present(
                    data,
                    'input_cost_per_million_tokens_usd',
                    'inputCostPerMillionTokensUsd',
                    default=0.0,
                ),
                0.0,
            ),
            output_cost_per_million_tokens_usd=_as_float(
                _first_present(
                    data,
                    'output_cost_per_million_tokens_usd',
                    'outputCostPerMillionTokensUsd',
                    default=0.0,
                ),
                0.0,
            ),
            cache_creation_input_cost_per_million_tokens_usd=_as_float(
                _first_present(
                    data,
                    'cache_creation_input_cost_per_million_tokens_usd',
                    'cacheCreationInputCostPerMillionTokensUsd',
                    default=0.0,
                ),
                0.0,
            ),
            cache_read_input_cost_per_million_tokens_usd=_as_float(
                _first_present(
                    data,
                    'cache_read_input_cost_per_million_tokens_usd',
                    'cacheReadInputCostPerMillionTokensUsd',
                    default=0.0,
                ),
                0.0,
            ),
        )


@dataclass(frozen=True)
class BudgetConfig:
    """运行期预算限制，用于保证安全和可预测性。"""

    max_total_tokens: int | None = None  # 会话 token 总量上限。
    max_input_tokens: int | None = None  # 输入 tokens 上限。
    max_output_tokens: int | None = None  # 输出 tokens 上限。
    max_reasoning_tokens: int | None = None  # 推理 tokens 上限。
    max_total_cost_usd: float | None = None  # 会话总成本上限（USD）。
    max_tool_calls: int | None = None  # 工具调用次数上限。
    max_delegated_tasks: int | None = None  # 子任务委托次数上限。
    max_model_calls: int | None = None  # 模型调用次数上限。
    max_session_turns: int | None = None  # 会话轮数上限。

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

    name: str  # 结构化输出名称。
    schema: JSONDict  # JSON Schema 定义。
    strict: bool = False  # 是否启用严格校验。

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

    model: str  # 模型标识。
    base_url: str = 'http://127.0.0.1:8000/v1'  # OpenAI-compatible 服务地址。
    api_key: str = 'local-token'  # API 密钥。
    temperature: float = 0.0  # 采样温度。
    timeout_seconds: float = 120.0  # 请求超时秒数。
    pricing: ModelPricing = field(default_factory=ModelPricing)  # 计费配置。

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

    allow_file_write: bool = False  # 允许写入文件。
    allow_shell_commands: bool = False  # 允许执行 shell 命令。
    allow_destructive_shell_commands: bool = False  # 允许执行高风险 shell 命令。

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

    cwd: Path  # 当前工作目录。
    max_turns: int = 12  # 最大对话轮数。
    command_timeout_seconds: float = 30.0  # 单次命令超时秒数。
    max_output_chars: int = 12000  # 命令输出最大字符数。
    stream_model_responses: bool = False  # 是否流式输出模型响应。
    auto_snip_threshold_tokens: int | None = None  # 自动裁剪触发阈值。
    auto_compact_threshold_tokens: int | None = None  # 自动压缩触发阈值。
    compact_preserve_messages: int = 4  # 压缩时保留的最近消息数。
    permissions: AgentPermissions = field(default_factory=AgentPermissions)  # 权限开关配置。
    additional_working_directories: tuple[Path, ...] = ()  # 允许访问的额外目录。
    disable_claude_md_discovery: bool = False  # 是否禁用 claude.md 自动发现。
    budget_config: BudgetConfig = field(default_factory=BudgetConfig)  # 预算限制配置。
    output_schema: OutputSchemaConfig | None = None  # 结构化输出 schema。
    session_directory: Path = field(default_factory=lambda: (Path('.port_sessions') / 'agent').resolve())  # 会话落盘目录。
    scratchpad_root: Path = field(default_factory=lambda: (Path('.port_sessions') / 'scratchpad').resolve())  # scratchpad 根目录。

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


@dataclass(frozen=True)
class ToolCall:
    """模型生成的一次工具调用。"""

    id: str  # 工具调用 ID。
    name: str  # 工具名称。
    arguments: JSONDict  # 工具参数。

    def to_dict(self) -> JSONDict:
        return {
            'id': self.id,
            'name': self.name,
            'arguments': dict(self.arguments),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ToolCall':
        data = _as_dict(payload)
        return cls(
            id=_as_str(data.get('id'), 'call_0'),
            name=_as_str(data.get('name'), 'unknown_tool'),
            arguments=_as_dict(data.get('arguments')),
        )


@dataclass(frozen=True)
class OneTurnResponse:
    """一次模型响应的标准化结果。"""

    content: str  # 本轮回复的最终文本内容。
    tool_calls: tuple[ToolCall, ...] = ()  # 本轮要求执行的工具调用列表。
    finish_reason: str | None = None  # 本轮停止原因，例如 stop 或 tool_calls。
    usage: TokenUsage = field(default_factory=TokenUsage)  # 本轮 token 使用统计。

    def to_dict(self) -> JSONDict:
        return {
            'content': self.content,
            'tool_calls': [item.to_dict() for item in self.tool_calls],
            'finish_reason': self.finish_reason,
            'usage': self.usage.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'OneTurnResponse':
        data = _as_dict(payload)
        tool_calls_raw = data.get('tool_calls', data.get('toolCalls', []))
        if not isinstance(tool_calls_raw, list):
            tool_calls_raw = []

        finish_reason_raw = _first_present(data, 'finish_reason', 'finishReason')
        finish_reason = (
            _as_str(finish_reason_raw) if finish_reason_raw is not None else None
        )

        return cls(
            content=_as_str(data.get('content'), ''),
            tool_calls=tuple(
                ToolCall.from_dict(item)
                for item in tool_calls_raw
                if isinstance(item, dict)
            ),
            finish_reason=finish_reason,
            usage=TokenUsage.from_dict(data.get('usage')),
        )


@dataclass(frozen=True)
class ToolExecutionResult:
    """工具处理函数返回的结构化结果。"""

    name: str  # 工具名称。
    ok: bool  # 工具执行是否成功。
    content: str  # 工具输出文本。
    metadata: JSONDict = field(default_factory=dict)  # 额外元数据。

    def to_dict(self) -> JSONDict:
        return {
            'name': self.name,
            'ok': self.ok,
            'content': self.content,
            'metadata': dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ToolExecutionResult':
        data = _as_dict(payload)
        return cls(
            name=_as_str(data.get('name'), 'unknown_tool'),
            ok=_as_bool(data.get('ok'), False),
            content=_as_str(data.get('content'), ''),
            metadata=_as_dict(data.get('metadata')),
        )


@dataclass(frozen=True)
class AgentRunResult:
    """一次 run 或 resume 调用产出的端到端结果。"""

    final_output: str  # 最终回复文本。
    turns: int  # 实际执行轮数。
    tool_calls: int  # 工具调用次数。
    transcript: tuple[JSONDict, ...]  # 对话转录。
    events: tuple[JSONDict, ...] = ()  # 运行事件列表。
    usage: TokenUsage = field(default_factory=TokenUsage)  # token 使用统计。
    total_cost_usd: float = 0.0  # 总成本（USD）。
    stop_reason: str | None = None  # 停止原因。
    file_history: tuple[JSONDict, ...] = ()  # 文件操作历史。
    session_id: str | None = None  # 会话 ID。
    session_path: str | None = None  # 会话文件路径。
    scratchpad_directory: str | None = None  # scratchpad 目录路径。

    def to_dict(self) -> JSONDict:
        return {
            'final_output': self.final_output,
            'turns': self.turns,
            'tool_calls': self.tool_calls,
            'transcript': [dict(item) for item in self.transcript],
            'events': [dict(item) for item in self.events],
            'usage': self.usage.to_dict(),
            'total_cost_usd': self.total_cost_usd,
            'stop_reason': self.stop_reason,
            'file_history': [dict(item) for item in self.file_history],
            'session_id': self.session_id,
            'session_path': self.session_path,
            'scratchpad_directory': self.scratchpad_directory,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'AgentRunResult':
        data = _as_dict(payload)
        transcript_raw = data.get('transcript', [])
        events_raw = data.get('events', [])
        file_history_raw = data.get('file_history', data.get('fileHistory', []))

        if not isinstance(transcript_raw, list):
            transcript_raw = []
        if not isinstance(events_raw, list):
            events_raw = []
        if not isinstance(file_history_raw, list):
            file_history_raw = []

        return cls(
            final_output=_as_str(data.get('final_output', data.get('finalOutput')), ''),
            turns=_as_int(data.get('turns'), 0),
            tool_calls=_as_int(data.get('tool_calls', data.get('toolCalls')), 0),
            transcript=tuple(item for item in transcript_raw if isinstance(item, dict)),
            events=tuple(item for item in events_raw if isinstance(item, dict)),
            usage=TokenUsage.from_dict(data.get('usage')),
            total_cost_usd=_as_float(data.get('total_cost_usd', data.get('totalCostUsd')), 0.0),
            stop_reason=(
                _as_str(data.get('stop_reason', data.get('stopReason')))
                if data.get('stop_reason', data.get('stopReason')) is not None
                else None
            ),
            file_history=tuple(item for item in file_history_raw if isinstance(item, dict)),
            session_id=(
                _as_str(data.get('session_id', data.get('sessionId')))
                if data.get('session_id', data.get('sessionId')) is not None
                else None
            ),
            session_path=(
                _as_str(data.get('session_path', data.get('sessionPath')))
                if data.get('session_path', data.get('sessionPath')) is not None
                else None
            ),
            scratchpad_directory=(
                _as_str(data.get('scratchpad_directory', data.get('scratchpadDirectory')))
                if data.get('scratchpad_directory', data.get('scratchpadDirectory')) is not None
                else None
            ),
        )
