"""ISSUE-007 会话落盘契约。

本模块定义会话持久化专用契约：
1) 保持与历史 JSON schema 的字段兼容。
2) 对缺失可选字段保持安全默认值。
3) 对核心字段缺失或类型异常抛出 ValueError。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..contract_types import AgentRuntimeConfig, JSONDict, ModelConfig, TokenUsage


def _first_present(data: JSONDict, *keys: str, default: Any = None) -> Any:
    """按顺序返回第一个存在且非 None 的字段值。"""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_int(value: Any, default: int = 0) -> int:
    """安全转换为 int。"""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    """安全转换为 float。"""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = '') -> str:
    """安全转换为 str。"""
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_dict(value: Any) -> JSONDict:
    """安全转换为 dict。"""
    if isinstance(value, dict):
        return dict(value)
    return {}


@dataclass(frozen=True)
class StoredAgentSession:
    """落盘后的代理会话快照。"""

    session_id: str  # 会话 ID。
    model_config: ModelConfig  # 会话使用的模型配置。
    runtime_config: AgentRuntimeConfig  # 会话使用的运行配置。
    messages: tuple[JSONDict, ...]  # 用于后续恢复上下文的原始消息。
    transcript: tuple[JSONDict, ...] = ()  # 审计用途的转录条目。
    events: tuple[JSONDict, ...] = ()  # 运行事件记录。
    final_output: str = ''  # 本次 run 结束时的最终输出。
    turns: int = 0  # 已执行轮数。
    tool_calls: int = 0  # 已执行工具调用数。
    usage: TokenUsage = field(default_factory=TokenUsage)  # 会话累计 usage。
    total_cost_usd: float = 0.0  # 会话累计成本。
    stop_reason: str | None = None  # 停止原因。
    file_history: tuple[JSONDict, ...] = ()  # 文件操作历史。
    scratchpad_directory: str | None = None  # scratchpad 目录路径。
    schema_version: int = 1  # 落盘 schema 版本。

    def to_dict(self) -> JSONDict:
        return {
            'schema_version': self.schema_version,
            'session_id': self.session_id,
            'model_config': self.model_config.to_dict(),
            'runtime_config': self.runtime_config.to_dict(),
            'messages': [dict(item) for item in self.messages],
            'transcript': [dict(item) for item in self.transcript],
            'events': [dict(item) for item in self.events],
            'final_output': self.final_output,
            'turns': self.turns,
            'tool_calls': self.tool_calls,
            'usage': self.usage.to_dict(),
            'total_cost_usd': self.total_cost_usd,
            'stop_reason': self.stop_reason,
            'file_history': [dict(item) for item in self.file_history],
            'scratchpad_directory': self.scratchpad_directory,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'StoredAgentSession':
        data = _as_dict(payload)
        session_id = _as_str(
            _first_present(data, 'session_id', 'sessionId', default=''),
            '',
        ).strip()
        if not session_id:
            raise ValueError('StoredAgentSession.session_id is required')

        model_payload = _first_present(data, 'model_config', 'modelConfig')
        if not isinstance(model_payload, dict):
            raise ValueError('StoredAgentSession.model_config must be a JSON object')

        runtime_payload = _first_present(data, 'runtime_config', 'runtimeConfig')
        if not isinstance(runtime_payload, dict):
            raise ValueError('StoredAgentSession.runtime_config must be a JSON object')

        messages_raw = data.get('messages')
        if not isinstance(messages_raw, list):
            raise ValueError('StoredAgentSession.messages must be a JSON array')

        transcript_raw = _first_present(data, 'transcript', default=[])
        events_raw = _first_present(data, 'events', default=[])
        file_history_raw = _first_present(data, 'file_history', 'fileHistory', default=[])

        if not isinstance(transcript_raw, list):
            transcript_raw = []
        if not isinstance(events_raw, list):
            events_raw = []
        if not isinstance(file_history_raw, list):
            file_history_raw = []

        stop_reason_raw = _first_present(data, 'stop_reason', 'stopReason')

        return cls(
            session_id=session_id,
            model_config=ModelConfig.from_dict(model_payload),
            runtime_config=AgentRuntimeConfig.from_dict(runtime_payload),
            messages=tuple(item for item in messages_raw if isinstance(item, dict)),
            transcript=tuple(item for item in transcript_raw if isinstance(item, dict)),
            events=tuple(item for item in events_raw if isinstance(item, dict)),
            final_output=_as_str(_first_present(data, 'final_output', 'finalOutput'), ''),
            turns=_as_int(data.get('turns'), 0),
            tool_calls=_as_int(_first_present(data, 'tool_calls', 'toolCalls'), 0),
            usage=TokenUsage.from_dict(data.get('usage')),
            total_cost_usd=_as_float(
                _first_present(data, 'total_cost_usd', 'totalCostUsd'),
                0.0,
            ),
            stop_reason=(
                _as_str(stop_reason_raw)
                if stop_reason_raw is not None
                else None
            ),
            file_history=tuple(item for item in file_history_raw if isinstance(item, dict)),
            scratchpad_directory=(
                _as_str(_first_present(data, 'scratchpad_directory', 'scratchpadDirectory'))
                if _first_present(data, 'scratchpad_directory', 'scratchpadDirectory') is not None
                else None
            ),
            schema_version=_as_int(
                _first_present(data, 'schema_version', 'schemaVersion', default=1),
                1,
            ),
        )
