"""静态运行策略契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .coercion import (
    _as_bool,
    _as_dict,
    _as_float,
    _as_int,
    _as_optional_int,
    _first_present,
    _path_or_default,
)
from .model import StructuredOutputSpec
from .protocol import JSONDict


@dataclass(frozen=True)
class WorkspaceScope:
    """描述 agent 启动前已知的工作区范围。"""

    cwd: Path
    additional_working_directories: tuple[Path, ...] = ()
    disable_claude_md_discovery: bool = False

    def to_dict(self) -> JSONDict:
        return {
            'cwd': str(self.cwd),
            'additional_working_directories': [str(path) for path in self.additional_working_directories],
            'disable_claude_md_discovery': self.disable_claude_md_discovery,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'WorkspaceScope':
        data = _as_dict(payload)
        additional_dirs_raw = data.get('additional_working_directories', data.get('additionalWorkingDirectories', []))
        if not isinstance(additional_dirs_raw, list):
            additional_dirs_raw = []
        return cls(
            cwd=_path_or_default(data.get('cwd'), Path('.').resolve()),
            additional_working_directories=tuple(
                Path(str(item)).resolve()
                for item in additional_dirs_raw
                if isinstance(item, str) and item.strip()
            ),
            disable_claude_md_discovery=_as_bool(
                _first_present(data, 'disable_claude_md_discovery', 'disableClaudeMdDiscovery'),
                False,
            ),
        )


@dataclass(frozen=True)
class ExecutionPolicy:
    """描述执行阶段的静态限制。"""

    max_turns: int = 12
    command_timeout_seconds: float = 30.0
    max_output_chars: int = 12000
    stream_model_responses: bool = False

    def to_dict(self) -> JSONDict:
        return {
            'max_turns': self.max_turns,
            'command_timeout_seconds': self.command_timeout_seconds,
            'max_output_chars': self.max_output_chars,
            'stream_model_responses': self.stream_model_responses,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ExecutionPolicy':
        data = _as_dict(payload)
        return cls(
            max_turns=_as_int(_first_present(data, 'max_turns', 'maxTurns', default=12), 12),
            command_timeout_seconds=_as_float(
                _first_present(data, 'command_timeout_seconds', 'commandTimeoutSeconds', default=30.0),
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
        )


@dataclass(frozen=True)
class ContextPolicy:
    """描述上下文治理与结构化输出策略。"""

    auto_snip_threshold_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    compact_preserve_messages: int = 4
    output_schema: StructuredOutputSpec | None = None

    def to_dict(self) -> JSONDict:
        return {
            'auto_snip_threshold_tokens': self.auto_snip_threshold_tokens,
            'auto_compact_threshold_tokens': self.auto_compact_threshold_tokens,
            'compact_preserve_messages': self.compact_preserve_messages,
            'output_schema': self.output_schema.to_dict() if self.output_schema else None,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ContextPolicy':
        data = _as_dict(payload)
        return cls(
            auto_snip_threshold_tokens=_as_optional_int(
                _first_present(data, 'auto_snip_threshold_tokens', 'autoSnipThresholdTokens')
            ),
            auto_compact_threshold_tokens=_as_optional_int(
                _first_present(data, 'auto_compact_threshold_tokens', 'autoCompactThresholdTokens')
            ),
            compact_preserve_messages=_as_int(
                _first_present(data, 'compact_preserve_messages', 'compactPreserveMessages', default=4),
                4,
            ),
            output_schema=StructuredOutputSpec.from_dict(
                _first_present(data, 'output_schema', 'outputSchema', default=None)
            ),
        )


@dataclass(frozen=True)
class SessionPaths:
    """描述会话与 scratchpad 的静态落盘路径。"""

    session_directory: Path = field(default_factory=lambda: (Path('.port_sessions') / 'agent').resolve())
    scratchpad_root: Path = field(default_factory=lambda: (Path('.port_sessions') / 'scratchpad').resolve())

    def to_dict(self) -> JSONDict:
        return {
            'session_directory': str(self.session_directory),
            'scratchpad_root': str(self.scratchpad_root),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'SessionPaths':
        data = _as_dict(payload)
        default_session_dir = (Path('.port_sessions') / 'agent').resolve()
        default_scratchpad_root = (Path('.port_sessions') / 'scratchpad').resolve()
        return cls(
            session_directory=_path_or_default(
                _first_present(data, 'session_directory', 'sessionDirectory'),
                default_session_dir,
            ),
            scratchpad_root=_path_or_default(
                _first_present(data, 'scratchpad_root', 'scratchpadRoot'),
                default_scratchpad_root,
            ),
        )