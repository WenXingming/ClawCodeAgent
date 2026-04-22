"""ISSUE-006 会话状态最小实现。

本模块只负责维护一轮 run 所需的最小会话状态：
1) 发送给模型的 messages。
2) 可追踪的 transcript。
3) 工具调用计数。

设计目标：结构简单、行为稳定、便于测试。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..contract_types import JSONDict, OneTurnResponse, ToolCall, ToolExecutionResult


@dataclass
class AgentSessionState:
    """代理运行中的会话状态。"""

    messages: list[JSONDict] = field(default_factory=list)  # 发给模型的消息列表。
    transcript_entries: list[JSONDict] = field(default_factory=list)  # 可追踪转录条目。
    tool_call_count: int = 0  # 已执行工具调用次数。

    @classmethod
    def create(cls, prompt: str) -> 'AgentSessionState':
        """按首条用户输入创建会话。"""
        session = cls()
        session.append_user(prompt)
        return session

    def append_user(self, prompt: str) -> None:
        """追加用户消息。"""
        message = {
            'role': 'user',
            'content': prompt,
        }
        self.messages.append(message)
        self.transcript_entries.append(dict(message))

    def append_assistant_turn(self, response: OneTurnResponse) -> None:
        """追加助手消息，并记录工具调用摘要。"""
        assistant_message: JSONDict = {
            'role': 'assistant',
            'content': response.content,
        }

        tool_calls_payload = [self._to_openai_tool_call_payload(call) for call in response.tool_calls]
        if tool_calls_payload:
            assistant_message['tool_calls'] = tool_calls_payload

        self.messages.append(assistant_message)
        self.transcript_entries.append(
            {
                'role': 'assistant',
                'content': response.content,
                'finish_reason': response.finish_reason,
                'tool_calls': [call.to_dict() for call in response.tool_calls],
                'usage': response.usage.to_dict(),
            }
        )

    def append_tool_result(self, tool_call: ToolCall, result: ToolExecutionResult) -> None:
        """追加工具结果消息。"""
        tool_message: JSONDict = {
            'role': 'tool',
            'tool_call_id': tool_call.id,
            'name': tool_call.name,
            'content': result.content,
        }
        self.messages.append(tool_message)

        # transcript 里保留执行状态与错误类型，方便定位问题。
        self.transcript_entries.append(
            {
                'role': 'tool',
                'tool_call_id': tool_call.id,
                'tool_name': tool_call.name,
                'content': result.content,
                'ok': result.ok,
                'metadata': dict(result.metadata),
            }
        )
        self.tool_call_count += 1

    def to_messages(self) -> list[JSONDict]:
        """返回当前可发送给模型的消息副本。"""
        return [dict(item) for item in self.messages]

    def transcript(self) -> tuple[JSONDict, ...]:
        """返回稳定的 transcript 元组。"""
        return tuple(dict(item) for item in self.transcript_entries)

    @staticmethod
    def _to_openai_tool_call_payload(tool_call: ToolCall) -> JSONDict:
        """把 ToolCall 转成标准 OpenAI tool_call 消息结构。"""
        return {
            'id': tool_call.id,
            'type': 'function',
            'function': {
                'name': tool_call.name,
                'arguments': json.dumps(tool_call.arguments, ensure_ascii=True),
            },
        }
