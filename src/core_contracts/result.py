"""端到端运行结果契约。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._coerce import _as_dict, _as_float, _as_int, _as_str
from .protocol import JSONDict
from .usage import TokenUsage


@dataclass(frozen=True)
class AgentRunResult:
    """一次 run 或 resume 调用产出的端到端结果。
    
    该对象包装完整运行的输出、诊断信息与成本统计，是 run/resume 调用的最终交付物。
    """

    final_output: str  # 最终生成的文本输出
    turns: int  # 本次运行完成的交互轮数
    tool_calls: int  # 本次运行调用工具的总次数
    transcript: tuple[JSONDict, ...]  # 事件转录记录（role/content/tool_* 消息）
    events: tuple[JSONDict, ...] = ()  # 运行过程中产生的事件（budget/snip/compact 等）
    usage: TokenUsage = field(default_factory=TokenUsage)  # 累计 token 使用统计
    total_cost_usd: float = 0.0  # 本次运行产生的总计费成本
    stop_reason: str | None = None  # 运行停止原因（normal/budget/token_limit/cost_limit等）
    file_history: tuple[JSONDict, ...] = ()  # 文件修改历史（可选）
    session_id: str | None = None  # 如果创建了会话，其对应 session ID
    session_path: str | None = None  # 持久化会话文件路径（如有）
    scratchpad_directory: str | None = None  # 临时工作目录路径（如有）

    def to_dict(self) -> JSONDict:
        """将运行结果序列化为字典。
        
        Returns:
            JSONDict: 包含所有字段的序列化字典
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
        """从字典反序列化运行结果。
        
        Args:
            payload (JSONDict | None): 待反序列化的字典，可为 None
            
        Returns:
            AgentRunResult: 反序列化后的运行结果对象
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