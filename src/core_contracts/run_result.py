"""定义一次完整运行结束后的端到端结果契约。

本模块负责描述 run 或 resume 调用结束后返回给上层的完整结果对象，包括最终输出、转录、事件、成本、文件历史与会话恢复信息。该对象是运行态向外部边界交付结果时使用的稳定数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .coercion import _as_dict, _as_float, _as_int, _as_str
from .protocol import JSONDict
from .token_usage import TokenUsage


@dataclass(frozen=True)
class AgentRunResult:
    """一次 run 或 resume 调用产出的端到端结果。

    该对象包装完整运行的输出、诊断信息与成本统计，是 `run` / `resume` 调用的最终交付物。外部通常通过 `to_dict()` 落盘或跨边界传输，并通过 `from_dict()` 在后续流程中恢复对象。
    """

    final_output: str  # str：运行结束后输出给用户的最终文本。
    turns: int  # int：本次运行累计执行的 turn 数。
    tool_calls: int  # int：本次运行累计执行的工具调用次数。
    transcript: tuple[JSONDict, ...]  # tuple[JSONDict, ...]：完整的可审计转录条目。
    events: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：运行期间记录的结构化事件流。
    usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：本次运行累计 token 使用量。
    total_cost_usd: float = 0.0  # float：本次运行累计估算成本，单位为美元。
    stop_reason: str | None = None  # str | None：运行结束的原因标识。
    file_history: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：本次运行产生的文件操作历史。
    session_id: str | None = None  # str | None：关联的会话唯一标识。
    session_path: str | None = None  # str | None：关联会话快照文件路径。
    scratchpad_directory: str | None = None  # str | None：本次运行使用的 scratchpad 目录。

    def to_dict(self) -> JSONDict:
        """把运行结果转换成 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 包含结果对象全部字段的可序列化字典。
        """
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
        """从 JSON 字典恢复端到端运行结果对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典，兼容 snake_case 与 camelCase 字段名。
        Returns:
            AgentRunResult: 恢复后的运行结果对象。
        """
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