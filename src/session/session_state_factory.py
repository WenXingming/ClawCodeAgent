"""session 运行态对象工厂。"""

from __future__ import annotations

from core_contracts.primitives import JSONDict
from core_contracts.session_contracts import AgentSessionState


class SessionStateFactory:
    """负责创建与恢复 AgentSessionState。"""

    def create(self, prompt: str) -> AgentSessionState:
        """基于首条用户输入创建运行态会话。
        Args:
            prompt (str): 首条用户输入。
        Returns:
            AgentSessionState: 初始化完成的运行态对象。
        Raises:
            ValueError: 当 prompt 非法时由 AgentSessionState 内部逻辑抛出。
        """
        return AgentSessionState.create(prompt)

    def resume(self, messages: tuple[JSONDict, ...], transcript: tuple[JSONDict, ...]) -> AgentSessionState:
        """基于持久化消息与转录恢复运行态会话。
        Args:
            messages (tuple[JSONDict, ...]): 持久化消息列表。
            transcript (tuple[JSONDict, ...]): 持久化转录列表。
        Returns:
            AgentSessionState: 恢复后的运行态对象。
        Raises:
            ValueError: 当输入结构非法时由 AgentSessionState 内部逻辑抛出。
        """
        return AgentSessionState.from_persisted(list(messages), list(transcript))
