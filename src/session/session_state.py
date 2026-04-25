"""维护代理单次运行期间的最小会话状态。

模块职责聚焦在一轮代理执行过程中最核心的三类数据：
1. 发给模型的消息上下文 `messages`。
2. 便于审计和恢复的转录条目 `transcript_entries`。
3. 用于预算和统计的工具调用计数 `tool_call_count`。

本模块不负责模型调用、工具执行或落盘持久化，只负责在内存中稳定维护会话状态，并向上层提供可序列化的消息/转录视图。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from core_contracts.protocol import JSONDict, OneTurnResponse, ToolCall, ToolExecutionResult


@dataclass
class AgentSessionState:
    """表示代理单次运行期间的可变会话状态。

    典型工作流如下：
    1. 调用 `create()` 用首条用户输入初始化会话。
    2. 每轮模型调用前通过 `to_messages()` 取出当前消息上下文。
    3. 模型返回后调用 `append_assistant_turn()` 写入助手响应。
    4. 工具执行后调用 `append_tool_result()` 追加工具输出。
    5. 需要审计或持久化时调用 `transcript()` 导出稳定转录视图。

    该类只维护运行态数据，不承担磁盘持久化职责。
    """

    messages: list[JSONDict] = field(default_factory=list)  # list[JSONDict]：发送给模型的完整消息上下文。
    transcript_entries: list[JSONDict] = field(default_factory=list)  # list[JSONDict]：按时间顺序记录的可审计转录条目。
    tool_call_count: int = 0  # int：当前会话中已经执行的工具调用次数。

    @classmethod
    def create(cls, prompt: str) -> 'AgentSessionState':
        """按首条用户输入创建会话。
        Args:
            prompt (str): 用户发起本轮会话时输入的首条提示词。
        Returns:
            AgentSessionState: 已写入首条用户消息的会话状态对象。
        """
        session = cls()
        session.append_user(prompt)
        return session

    def append_user(self, prompt: str) -> None:
        """向会话中追加一条用户消息。
        Args:
            prompt (str): 用户输入的自然语言内容。
        Returns:
            None: 该方法直接原地更新 `messages` 和 `transcript_entries`。
        """
        message = {
            'role': 'user',
            'content': prompt,
        }
        self.messages.append(message)
        self.transcript_entries.append(dict(message))

    def append_assistant_turn(self, response: OneTurnResponse) -> None:
        """向会话中追加一轮助手响应。

        该方法会同时维护两份视图：
        1. `messages` 中的标准 assistant message，用于下一轮继续发给模型。
        2. `transcript_entries` 中的审计记录，用于保留 finish reason、tool calls 和 usage。

        Args:
            response (OneTurnResponse): 模型返回的一轮标准化响应对象。
        Returns:
            None: 该方法直接原地更新会话状态。
        """
        # 把 OpenAI 兼容的 assistant message 添加到消息上下文
        assistant_message: JSONDict = {
            'role': 'assistant',
            'content': response.content,
        }
        self.messages.append(assistant_message)

        # 如果本轮有工具调用，则把它们也添加到消息上下文，保持与 OpenAI 格式兼容。
        tool_calls_payload = [
            self._to_openai_tool_call_payload(call)
            for call in response.tool_calls
        ]
        if tool_calls_payload:
            assistant_message['tool_calls'] = tool_calls_payload

        # 把本轮的完整响应添加到审计记录中，保留 finish reason、tool calls 和 usage 等关键信息。
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
        """向会话中追加一条工具执行结果。
        Args:
            tool_call (ToolCall): 触发本次工具执行的工具调用描述。
            result (ToolExecutionResult): 工具执行后的标准化结果。
        Returns:
            None: 该方法直接原地更新消息、转录和工具调用计数。
        """
        # 把 OpenAI 兼容的 tool message 添加到消息上下文
        tool_message: JSONDict = {
            'role': 'tool',
            'tool_call_id': tool_call.id,
            'name': tool_call.name,
            'content': result.content,
        }
        self.messages.append(tool_message)

        # 把工具调用结果添加到审计记录中，保留工具调用 ID 和结果内容。
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
        """导出当前可继续发送给模型的消息副本。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            list[JSONDict]: `messages` 的浅拷贝列表，调用方可安全读取。
        """
        return [dict(item) for item in self.messages]

    def transcript(self) -> tuple[JSONDict, ...]:
        """导出稳定的转录只读视图。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[JSONDict, ...]: 以元组形式返回的转录条目副本，适合审计或持久化。
        """
        return tuple(dict(item) for item in self.transcript_entries)

    @classmethod
    def from_persisted(
        cls,
        messages: list[JSONDict],
        transcript: list[JSONDict],
        tool_call_count: int,
    ) -> 'AgentSessionState':
        """从已持久化的数据恢复运行态会话。

        若历史 transcript 为空，则使用 `messages` 生成最小可审计条目作为回退，保证恢复后的会话仍具备连续的转录视图。
        Args:
            messages (list[JSONDict]): 恢复时使用的历史消息列表。
            transcript (list[JSONDict]): 已持久化的历史转录条目。
            tool_call_count (int): 已执行的工具调用累计次数。
        Returns:
            AgentSessionState: 从持久化数据恢复出的运行态会话对象。
        """
        if transcript:
            effective_transcript: list[JSONDict] = [dict(item) for item in transcript]
        else:
            effective_transcript = [
                {'role': item['role'], 'content': item.get('content', '')}
                for item in messages
                if isinstance(item, dict) and 'role' in item
            ]
        return cls(
            messages=[dict(item) for item in messages],
            transcript_entries=effective_transcript,
            tool_call_count=tool_call_count,
        )

    @staticmethod
    def _to_openai_tool_call_payload(tool_call: ToolCall) -> JSONDict:
        """把内部 ToolCall 转成 OpenAI 兼容的 `tool_calls` 消息结构。
        Args:
            tool_call (ToolCall): 内部标准化后的工具调用对象。
        Returns:
            JSONDict: 可直接放入 assistant message 的 OpenAI 兼容 tool_call 载荷。
        """
        return {
            'id': tool_call.id,
            'type': 'function',
            'function': {
                'name': tool_call.name,
                'arguments': json.dumps(tool_call.arguments, ensure_ascii=True),
            },
        }