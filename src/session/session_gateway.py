"""session 领域唯一公开网关。

外部模块只允许通过 SessionGateway 访问会话能力，
会话状态与快照类型统一来自 core_contracts.session。
"""

from __future__ import annotations

from pathlib import Path

from core_contracts.session import AgentSessionSnapshot, AgentSessionState
from .session_store import AgentSessionStore


class SessionGateway:
    """代理会话管理的唯一公开网关。"""

    def __init__(self, session_store_directory: Path | None = None) -> None:
        """初始化会话网关。
        Args:
            session_store_directory (Path | None): 会话快照目录；None 时使用默认目录。
        Returns:
            None
        Raises:
            无。
        """
        self._store = AgentSessionStore(directory=session_store_directory)  # AgentSessionStore：内部会话快照存取器。

    @property
    def directory(self) -> Path:
        """返回会话存储目录。
        Args:
            无
        Returns:
            Path: 当前会话存储目录。
        Raises:
            无。
        """
        return self._store.directory

    def save_session(self, snapshot: AgentSessionSnapshot) -> Path:
        """保存会话快照到磁盘。
        Args:
            snapshot (AgentSessionSnapshot): 待保存快照对象。
        Returns:
            Path: 写入的快照文件路径。
        Raises:
            ValueError: 当 session_id 非法时抛出。
        """
        return self._store.save(snapshot)

    def load_session(self, session_id: str) -> AgentSessionSnapshot:
        """按 session_id 从磁盘恢复会话快照。
        Args:
            session_id (str): 会话唯一标识。
        Returns:
            AgentSessionSnapshot: 恢复出的会话快照。
        Raises:
            ValueError: 当会话不存在或文件损坏时抛出。
        """
        return self._store.load(session_id)

    def create_session_state(self, prompt: str) -> AgentSessionState:
        """为新会话创建运行时状态。
        Args:
            prompt (str): 首条用户输入。
        Returns:
            AgentSessionState: 初始化后的运行态对象。
        Raises:
            无。
        """
        return AgentSessionState.create(prompt)

    def restore_session_state(
        self,
        messages: list[dict],
        transcript: list[dict],
    ) -> AgentSessionState:
        """从持久化数据恢复运行时会话状态。
        Args:
            messages (list[dict]): 持久化消息列表。
            transcript (list[dict]): 持久化转录列表。
        Returns:
            AgentSessionState: 恢复后的运行态对象。
        Raises:
            无。
        """
        return AgentSessionState.from_persisted(messages, transcript)

__all__ = [
    'SessionGateway',
]
