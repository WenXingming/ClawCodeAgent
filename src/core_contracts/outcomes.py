"""运行与查询结果跨模块契约。

合并 run_result.py 与 app_contracts.py，
定义 AgentRunResult (端到端运行结果)、QueryServiceConfig 和 QueryTurnResult。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._coercion import _as_dict, _as_float, _as_int, _as_str
from .primitives import JSONDict, TokenUsage


# ── 端到端运行结果 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentRunResult:
    """一次 run 或 resume 调用产出的端到端结果。"""

    final_output: str  # str：运行结束后输出给用户的最终文本。
    turns: int  # int：本次运行累计执行的 turn 数。
    tool_calls: int  # int：本次运行累计执行的工具调用次数。
    transcript: tuple[JSONDict, ...]  # tuple[JSONDict, ...]：完整的可审计转录条目。
    events: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：运行期间记录的结构化事件流。
    usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：本次运行累计 token 使用量。
    total_cost_usd: float = 0.0  # float：本次运行累计估算成本（美元）。
    stop_reason: str | None = None  # str | None：运行结束的原因标识。
    file_history: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：本次运行产生的文件操作历史。
    session_id: str | None = None  # str | None：关联的会话唯一标识。
    session_path: str | None = None  # str | None：关联会话快照文件路径。
    scratchpad_directory: str | None = None  # str | None：本次运行使用的 scratchpad 目录。

    def to_dict(self) -> JSONDict:
        """把运行结果转换成 JSON 字典。
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
            payload (JSONDict | None): 待反序列化的原始字典，兼容 camelCase 字段名。
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


# ── 查询服务 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QueryServiceConfig:
    """QueryService 的轻量配置集合。"""

    include_runtime_summary_event: bool = True  # bool：是否在 stream_submit 末尾附加 runtime_summary 事件。

    def to_dict(self) -> JSONDict:
        """把配置序列化为字典。
        Returns:
            JSONDict: 包含全部配置字段的可序列化字典。
        """
        return {'include_runtime_summary_event': self.include_runtime_summary_event}


@dataclass(frozen=True)
class QueryTurnResult:
    """QueryService 单次 submit / stream_submit 对外暴露的稳定结果。"""

    prompt: str  # str：本轮用户输入原文。
    output: str  # str：本轮 agent 最终输出文本。
    usage: TokenUsage  # TokenUsage：本轮增量 token 使用量。
    usage_total: TokenUsage  # TokenUsage：会话累计 token 使用量。
    stop_reason: str  # str：本轮结束原因标识。
    session_id: str | None = None  # str | None：关联的会话唯一标识。
    session_path: str | None = None  # str | None：本轮会话快照文件路径。
    tool_calls: int = 0  # int：本轮累计工具调用次数。
    total_cost_usd: float = 0.0  # float：本轮累计估算成本（美元）。
    events: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：本轮运行期间记录的结构化事件流。
    transcript: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：本轮完整可审计转录条目。

    def to_dict(self) -> JSONDict:
        """把单轮结果转换为 JSON 字典。
        Returns:
            JSONDict: 包含全部字段的可序列化字典。
        """
        return {
            'prompt': self.prompt,
            'output': self.output,
            'usage': self.usage.to_dict(),
            'usage_total': self.usage_total.to_dict(),
            'stop_reason': self.stop_reason,
            'session_id': self.session_id,
            'session_path': self.session_path,
            'tool_calls': self.tool_calls,
            'total_cost_usd': self.total_cost_usd,
            'events': [dict(item) for item in self.events],
            'transcript': [dict(item) for item in self.transcript],
        }
