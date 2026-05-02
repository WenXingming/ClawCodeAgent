"""运行时策略与权限配置契约。

合并 permissions.py、budget.py 与 runtime_policy.py，
集中定义工作区范围、执行限制、上下文治理、预算控制与权限开关。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._coercion import (
    _as_bool,
    _as_dict,
    _as_float,
    _as_int,
    _as_optional_float,
    _as_optional_int,
    _first_present,
    _path_or_default,
)
from .model import StructuredOutputSpec
from .primitives import JSONDict


# ── 工作区范围 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkspaceScope:
    """描述 agent 启动前已知的工作区范围。"""

    cwd: Path  # Path：当前工作区根目录。
    additional_working_directories: tuple[Path, ...] = ()  # tuple[Path, ...]：额外工作目录。
    disable_claude_md_discovery: bool = False  # bool：是否禁用手册发现。

    def to_dict(self) -> JSONDict:
        """把工作区范围配置序列化为字典。

        Returns:
            JSONDict: 包含 cwd、additional_working_directories 和 disable_claude_md_discovery 的字典。
        """
        return {
            'cwd': str(self.cwd),
            'additional_working_directories': [str(path) for path in self.additional_working_directories],
            'disable_claude_md_discovery': self.disable_claude_md_discovery,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'WorkspaceScope':
        """从 JSON 字典恢复工作区范围配置。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典，兼容 camelCase 字段名。
        Returns:
            WorkspaceScope: 恢复后的工作区范围配置对象。
        """
        data = _as_dict(payload)
        additional_dirs_raw = data.get('additional_working_directories', data.get('additionalWorkingDirectories', []))
        if not isinstance(additional_dirs_raw, list):
            additional_dirs_raw = []
        return cls(
            cwd=_path_or_default(data.get('cwd'), Path('.').resolve()),
            additional_working_directories=tuple(
                Path(str(item)).resolve() for item in additional_dirs_raw if isinstance(item, str) and item.strip()
            ),
            disable_claude_md_discovery=_as_bool(
                _first_present(data, 'disable_claude_md_discovery', 'disableClaudeMdDiscovery'), False
            ),
        )


# ── 执行策略 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionPolicy:
    """描述执行阶段的静态限制。"""

    max_turns: int = 12  # int：单次 run 的最大模型轮次。
    command_timeout_seconds: float = 30.0  # float：shell 命令超时时间，单位秒。
    max_output_chars: int = 12000  # int：工具输出最大字符数。
    stream_model_responses: bool = False  # bool：是否启用流式模型响应。

    def to_dict(self) -> JSONDict:
        """把执行策略序列化为字典。

        Returns:
            JSONDict: 包含 max_turns、command_timeout_seconds、max_output_chars 和 stream_model_responses 的字典。
        """
        return {
            'max_turns': self.max_turns,
            'command_timeout_seconds': self.command_timeout_seconds,
            'max_output_chars': self.max_output_chars,
            'stream_model_responses': self.stream_model_responses,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ExecutionPolicy':
        """从 JSON 字典恢复执行策略配置。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典，兼容 camelCase 字段名。
        Returns:
            ExecutionPolicy: 恢复后的执行策略对象。
        """
        data = _as_dict(payload)
        return cls(
            max_turns=_as_int(_first_present(data, 'max_turns', 'maxTurns', default=12), 12),
            command_timeout_seconds=_as_float(
                _first_present(data, 'command_timeout_seconds', 'commandTimeoutSeconds', default=30.0), 30.0
            ),
            max_output_chars=_as_int(
                _first_present(data, 'max_output_chars', 'maxOutputChars', default=12000), 12000
            ),
            stream_model_responses=_as_bool(
                _first_present(data, 'stream_model_responses', 'streamModelResponses'), False
            ),
        )


# ── 上下文策略 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContextPolicy:
    """描述上下文治理与结构化输出策略。"""

    auto_snip_threshold_tokens: int | None = None  # int | None：自动 snip 的 token 阈值。
    auto_compact_threshold_tokens: int | None = None  # int | None：自动 compact 的 token 阈值。
    compact_preserve_messages: int = 4  # int：compact 保留的尾部消息数。
    output_schema: StructuredOutputSpec | None = None  # StructuredOutputSpec | None：结构化输出 schema。

    def to_dict(self) -> JSONDict:
        """把上下文策略序列化为字典。

        Returns:
            JSONDict: 包含 auto_snip/auto_compact 阈值、compact_preserve_messages 和 output_schema 的字典。
        """
        return {
            'auto_snip_threshold_tokens': self.auto_snip_threshold_tokens,
            'auto_compact_threshold_tokens': self.auto_compact_threshold_tokens,
            'compact_preserve_messages': self.compact_preserve_messages,
            'output_schema': self.output_schema.to_dict() if self.output_schema else None,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ContextPolicy':
        """从 JSON 字典恢复上下文策略配置。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典，兼容 camelCase 字段名。
        Returns:
            ContextPolicy: 恢复后的上下文策略对象。
        """
        data = _as_dict(payload)
        return cls(
            auto_snip_threshold_tokens=_as_optional_int(
                _first_present(data, 'auto_snip_threshold_tokens', 'autoSnipThresholdTokens')
            ),
            auto_compact_threshold_tokens=_as_optional_int(
                _first_present(data, 'auto_compact_threshold_tokens', 'autoCompactThresholdTokens')
            ),
            compact_preserve_messages=_as_int(
                _first_present(data, 'compact_preserve_messages', 'compactPreserveMessages', default=4), 4
            ),
            output_schema=StructuredOutputSpec.from_dict(
                _first_present(data, 'output_schema', 'outputSchema', default=None)
            ),
        )


# ── 会话路径 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SessionPaths:
    """描述会话与 scratchpad 的静态落盘路径。"""

    session_directory: Path = field(default_factory=lambda: (Path('.port_sessions') / 'agent').resolve())  # Path：会话快照目录。
    scratchpad_root: Path = field(default_factory=lambda: (Path('.port_sessions') / 'scratchpad').resolve())  # Path：沙盒根目录。

    def to_dict(self) -> JSONDict:
        """把会话路径配置序列化为字典。

        Returns:
            JSONDict: 包含 session_directory 和 scratchpad_root 的字典。
        """
        return {
            'session_directory': str(self.session_directory),
            'scratchpad_root': str(self.scratchpad_root),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'SessionPaths':
        """从 JSON 字典恢复会话路径配置。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典，兼容 camelCase 字段名。
        Returns:
            SessionPaths: 恢复后的会话路径对象。
        """
        data = _as_dict(payload)
        default_session_dir = (Path('.port_sessions') / 'agent').resolve()
        default_scratchpad_root = (Path('.port_sessions') / 'scratchpad').resolve()
        return cls(
            session_directory=_path_or_default(
                _first_present(data, 'session_directory', 'sessionDirectory'), default_session_dir
            ),
            scratchpad_root=_path_or_default(
                _first_present(data, 'scratchpad_root', 'scratchpadRoot'), default_scratchpad_root
            ),
        )


# ── 预算配置 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BudgetConfig:
    """描述会话运行期间的预算限制。"""

    max_total_tokens: int | None = None  # int | None：总 token 硬上限。
    max_input_tokens: int | None = None  # int | None：输入 token 软上限。
    max_output_tokens: int | None = None  # int | None：输出 token 预算。
    max_reasoning_tokens: int | None = None  # int | None：推理 token 预算。
    max_total_cost_usd: float | None = None  # float | None：最大允许成本（美元）。
    max_tool_calls: int | None = None  # int | None：最大工具调用次数。
    max_delegated_tasks: int | None = None  # int | None：最大委托任务数。
    max_model_calls: int | None = None  # int | None：最大模型调用次数。
    max_session_turns: int | None = None  # int | None：最大会话轮次数。

    def to_dict(self) -> JSONDict:
        """把预算配置序列化为字典。

        Returns:
            JSONDict: 包含全部预算限制字段的字典。
        """
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
        """从 JSON 字典恢复预算配置。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典，兼容 camelCase 字段名。
        Returns:
            BudgetConfig: 恢复后的预算配置对象。
        """
        data = _as_dict(payload)
        return cls(
            max_total_tokens=_as_optional_int(_first_present(data, 'max_total_tokens', 'maxTotalTokens')),
            max_input_tokens=_as_optional_int(_first_present(data, 'max_input_tokens', 'maxInputTokens')),
            max_output_tokens=_as_optional_int(_first_present(data, 'max_output_tokens', 'maxOutputTokens')),
            max_reasoning_tokens=_as_optional_int(_first_present(data, 'max_reasoning_tokens', 'maxReasoningTokens')),
            max_total_cost_usd=_as_optional_float(_first_present(data, 'max_total_cost_usd', 'maxTotalCostUsd')),
            max_tool_calls=_as_optional_int(_first_present(data, 'max_tool_calls', 'maxToolCalls')),
            max_delegated_tasks=_as_optional_int(_first_present(data, 'max_delegated_tasks', 'maxDelegatedTasks')),
            max_model_calls=_as_optional_int(_first_present(data, 'max_model_calls', 'maxModelCalls')),
            max_session_turns=_as_optional_int(_first_present(data, 'max_session_turns', 'maxSessionTurns')),
        )
