"""模型消息协议与工具执行结果契约。

定义 ToolCall、OneTurnResponse、StreamEvent、ToolExecutionResult 等
模型交互与工具调用链路中的核心数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._coercion import (
    _as_bool,
    _as_dict,
    _as_int,
    _as_optional_int,
    _as_optional_str,
    _as_str,
    _first_present,
)
from .primitives import JSONDict, TokenUsage


@dataclass(frozen=True)
class ToolCall:
    """模型生成的一次工具调用。"""

    id: str  # str：工具调用的全局唯一 ID。
    name: str  # str：工具名称。
    arguments: JSONDict  # JSONDict：工具参数字典。

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        Returns:
            JSONDict: 包含 id/name/arguments 的字典。
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
            payload (JSONDict | None): 待反序列化的字典。
        Returns:
            ToolCall: 反序列化后的工具调用对象。
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

    content: str  # str：模型正文内容。
    tool_calls: tuple[ToolCall, ...] = ()  # tuple[ToolCall, ...]：工具调用序列。
    finish_reason: str | None = None  # str | None：完成原因。
    usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：本响应的 token 统计。

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        Returns:
            JSONDict: 包含全部字段的字典。
        """
        return {
            'content': self.content,
            'tool_calls': [item.to_dict() for item in self.tool_calls],
            'finish_reason': self.finish_reason,
            'usage': self.usage.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'OneTurnResponse':
        """反序列化为 OneTurnResponse。
        Args:
            payload (JSONDict | None): 原始响应字典。
        Returns:
            OneTurnResponse: 标准化响应对象。
        """
        data = _as_dict(payload)
        tool_calls_raw = data.get('tool_calls', data.get('toolCalls', []))
        if not isinstance(tool_calls_raw, list):
            tool_calls_raw = []

        finish_reason_raw = _first_present(data, 'finish_reason', 'finishReason')
        finish_reason = _as_str(finish_reason_raw) if finish_reason_raw is not None else None

        return cls(
            content=_as_str(data.get('content'), ''),
            tool_calls=tuple(ToolCall.from_dict(item) for item in tool_calls_raw if isinstance(item, dict)),
            finish_reason=finish_reason,
            usage=TokenUsage.from_dict(data.get('usage')),
        )


@dataclass(frozen=True)
class StreamEvent:
    """流式返回中的标准化事件。"""

    type: str  # str：事件类型（text_delta / tool_call_start / tool_call_delta / finish）。
    delta: str = ''  # str：text_delta 事件的增量内容。
    tool_call_index: int | None = None  # int | None：工具调用索引。
    tool_call_id: str | None = None  # str | None：工具调用 ID。
    tool_name: str | None = None  # str | None：工具名称。
    arguments_delta: str = ''  # str：工具参数的 JSON 增量字符串。
    finish_reason: str | None = None  # str | None：完成原因。
    usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：本事件 token 统计。
    raw_event: JSONDict = field(default_factory=dict)  # JSONDict：原始 API 响应事件。

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        Returns:
            JSONDict: 包含全部字段的字典。
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
        """反序列化为 StreamEvent。
        Args:
            payload (JSONDict | None): API 返回的原始事件字典。
        Returns:
            StreamEvent: 标准化事件对象。
        """
        data = _as_dict(payload)
        finish_reason_raw = _first_present(data, 'finish_reason', 'finishReason')
        return cls(
            type=_as_str(data.get('type'), 'unknown'),
            delta=_as_str(data.get('delta'), ''),
            tool_call_index=_as_optional_int(_first_present(data, 'tool_call_index', 'toolCallIndex')),
            tool_call_id=_as_optional_str(_first_present(data, 'tool_call_id', 'toolCallId')),
            tool_name=_as_optional_str(_first_present(data, 'tool_name', 'toolName')),
            arguments_delta=_as_str(_first_present(data, 'arguments_delta', 'argumentsDelta', default='')),
            finish_reason=_as_optional_str(finish_reason_raw),
            usage=TokenUsage.from_dict(data.get('usage')),
            raw_event=_as_dict(data.get('raw_event', data.get('rawEvent'))),
        )


@dataclass(frozen=True)
class ToolExecutionResult:
    """工具处理函数返回的结构化结果。"""

    name: str  # str：工具名称。
    ok: bool  # bool：是否执行成功。
    content: str  # str：工具返回的主内容。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict：额外元数据。

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        Returns:
            JSONDict: 包含 name/ok/content/metadata 的字典。
        """
        return {
            'name': self.name,
            'ok': self.ok,
            'content': self.content,
            'metadata': dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ToolExecutionResult':
        """反序列化为 ToolExecutionResult。
        Args:
            payload (JSONDict | None): 待反序列化的字典。
        Returns:
            ToolExecutionResult: 工具执行结果对象。
        """
        data = _as_dict(payload)
        return cls(
            name=_as_str(data.get('name'), 'unknown_tool'),
            ok=_as_bool(data.get('ok'), False),
            content=_as_str(data.get('content'), ''),
            metadata=_as_dict(data.get('metadata')),
        )
