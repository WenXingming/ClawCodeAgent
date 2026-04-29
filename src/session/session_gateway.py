"""session 域统一网关。"""

from __future__ import annotations

from pathlib import Path

from .session_snapshot import AgentSessionSnapshot
from .session_state import AgentSessionState
from .session_store import AgentSessionStore


class SessionGateway:
    """代理会话管理的唯一公开网关。"""

    def __init__(self, session_store_directory: Path | None = None) -> None:
        self._store = AgentSessionStore(directory=session_store_directory)

    @property
    def directory(self) -> Path:
        """返回会话存储目录（用于向后兼容性）。"""
        return self._store.directory

    def save_session(self, snapshot: AgentSessionSnapshot) -> Path:
        """保存会话快照到磁盘。"""
        return self._store.save(snapshot)

    def load_session(self, session_id: str) -> AgentSessionSnapshot:
        """按 session_id 从磁盘恢复会话快照。"""
        return self._store.load(session_id)

    def create_session_state(self, prompt: str) -> AgentSessionState:
        """为新会话创建运行时状态。"""
        return AgentSessionState.create(prompt)

    def restore_session_state(
        self,
        messages: list[dict],
        transcript: list[dict],
    ) -> AgentSessionState:
        """从持久化数据恢复运行时会话状态。"""
        return AgentSessionState.from_persisted(messages, transcript)


# 兼容旧命名，后续可删除。
SessionManager = SessionGateway


__all__ = [
    'SessionGateway',
    'SessionManager',
    'AgentSessionSnapshot',
    'AgentSessionState',
]
