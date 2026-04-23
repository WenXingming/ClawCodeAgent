"""端到端运行结果契约。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._coerce import _as_dict, _as_float, _as_int, _as_str
from .protocol import JSONDict
from .usage import TokenUsage


@dataclass(frozen=True)
class AgentRunResult:
    """一次 run 或 resume 调用产出的端到端结果。"""

    final_output: str
    turns: int
    tool_calls: int
    transcript: tuple[JSONDict, ...]
    events: tuple[JSONDict, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    total_cost_usd: float = 0.0
    stop_reason: str | None = None
    file_history: tuple[JSONDict, ...] = ()
    session_id: str | None = None
    session_path: str | None = None
    scratchpad_directory: str | None = None

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