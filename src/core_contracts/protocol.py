"""模型与工具交互协议相关契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JSONDict = dict[str, Any]

from ._coerce import (
    _as_bool,
    _as_dict,
    _as_int,
    _as_optional_int,
    _as_optional_str,
    _as_str,
    _first_present,
)
from .usage import TokenUsage


@dataclass(frozen=True)
class ToolCall:
    """模型生成的一次工具调用。
    
    表示模型调用外部工具的一次输出。
    """

    id: str  # 工具调用的全局唯一 ID
    name: str  # 工具名称
    arguments: JSONDict  # 工具参数字典

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        
        Returns:
            JSONDict: 包含 id/name/arguments 的字典
        """
        return {
            'id': self.id,
            'name': self.name,
            'arguments': dict(self.arguments),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ToolCall':
        """反序列化为 ToolCall 对象。
        
        Args:
            payload (JSONDict | None): 待反序列化的字典
            
        Returns:
            ToolCall: 反序列化后的工具调用对象
        """
        data = _as_dict(payload)
        return cls(
            id=_as_str(data.get('id'), 'call_0'),
            name=_as_str(data.get('name'), 'unknown_tool'),
            arguments=_as_dict(data.get('arguments')),
        )


@dataclass(frozen=True)
class OneTurnResponse:
    """一次模型响应的标准化结果。"""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)

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
        finish_reason = _as_str(finish_reason_raw) if finish_reason_raw is not None else None

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
class StreamEvent:
    """流式返回中的标准化事件。
    
    表示从模型流式API返回的单个事件。支持content delta、tool call增量、完成信号等。
    """

    type: str  # 事件类型，如 'text_delta'、'tool_call_start'、'tool_call_delta'、'finish'
    delta: str = ''  # 对于text_delta事件，此字段包含增量内容
    tool_call_index: int | None = None  # 当前工具调用的索引（从0开始）
    tool_call_id: str | None = None  # 工具调用的全局唯一ID
    tool_name: str | None = None  # 工具名称
    arguments_delta: str = ''  # 工具参数的JSON增量字符串
    finish_reason: str | None = None  # 完成原因，如 'stop'、'length'、'tool_calls'等
    usage: TokenUsage = field(default_factory=TokenUsage)  # 本事件的token消耗统计
    raw_event: JSONDict = field(default_factory=dict)  # 原始API响应事件（用于调试）

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        
        Returns:
            JSONDict: 包含所有字段的字典
        """
        return {
            'type': self.type,
            'delta': self.delta,
            'tool_call_index': self.tool_call_index,
            'tool_call_id': self.tool_call_id,
            'tool_name': self.tool_name,
            'arguments_delta': self.arguments_delta,
            'finish_reason': self.finish_reason,
            'usage': self.usage.to_dict(),
            'raw_event': dict(self.raw_event),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'StreamEvent':
        """反序列化为 StreamEvent 对象。
        
        Args:
            payload (JSONDict | None): API返回的原始事件字典
            
        Returns:
            StreamEvent: 反序列化后的事件对象
        """
        data = _as_dict(payload)
        finish_reason_raw = _first_present(data, 'finish_reason', 'finishReason')
        return cls(
            type=_as_str(data.get('type'), 'unknown'),
            delta=_as_str(data.get('delta'), ''),
            tool_call_index=_as_optional_int(
                _first_present(data, 'tool_call_index', 'toolCallIndex')
            ),
            tool_call_id=_as_optional_str(
                _first_present(data, 'tool_call_id', 'toolCallId')
            ),
            tool_name=_as_optional_str(_first_present(data, 'tool_name', 'toolName')),
            arguments_delta=_as_str(
                _first_present(data, 'arguments_delta', 'argumentsDelta', default='')
            ),
            finish_reason=_as_optional_str(finish_reason_raw),
            usage=TokenUsage.from_dict(data.get('usage')),
            raw_event=_as_dict(data.get('raw_event', data.get('rawEvent'))),
        )


@dataclass(frozen=True)
class ToolExecutionResult:
    """工具处理函数返回的结构化结果。
    
    表示本地工具执行后的返回值。包含执行是否成功的标志、内容以及元数据。
    """

    name: str  # 工具名称
    ok: bool  # 是否执行成功
    content: str  # 工具返回的主内容（通常为文本）
    metadata: JSONDict = field(default_factory=dict)  # 额外元数据（如返回码、输出流等）

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        
        Returns:
            JSONDict: 包含 name/ok/content/metadata 的字典
        """
        return {
            'name': self.name,
            'ok': self.ok,
            'content': self.content,
            'metadata': dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ToolExecutionResult':
        """反序列化为 ToolExecutionResult 对象。
        
        Args:
            payload (JSONDict | None): 待反序列化的字典
            
        Returns:
            ToolExecutionResult: 反序列化后的工具执行结果
        """
        data = _as_dict(payload)
        return cls(
            name=_as_str(data.get('name'), 'unknown_tool'),
            ok=_as_bool(data.get('ok'), False),
            content=_as_str(data.get('content'), ''),
            metadata=_as_dict(data.get('metadata')),
        )