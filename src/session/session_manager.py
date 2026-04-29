"""会话管理的唯一公开入口。

该模块提供 SessionManager Facade，作为整个会话子系统对外暴露的唯一窗口。
外层模块只依赖 SessionManager，不直接导入 SessionSnapshot、SessionState 或 SessionStore。

内部仍然使用 session_snapshot、session_state、session_store 的具体实现，
但所有复杂性都被封装在 SessionManager 内部。
"""

from __future__ import annotations

from pathlib import Path

from .session_snapshot import AgentSessionSnapshot
from .session_state import AgentSessionState
from .session_store import AgentSessionStore


class SessionManager:
    """代理会话管理的唯一公开 Facade。

    该类作为会话子系统的公开门面，负责：
    1. 会话持久化与恢复（通过 SessionStore）
    2. 会话运行时状态管理（通过 SessionState）
    3. 会话快照的序列化与反序列化（通过 SessionSnapshot）

    外层代码只依赖 SessionManager，保持对会话内部实现的完全隔离。
    """

    def __init__(self, session_store_directory: Path | None = None) -> None:
        """初始化会话管理器。

        Args:
            session_store_directory (Path | None): 自定义的会话存储目录；
                                                   为 None 时使用默认目录。
        Returns:
            None: 该方法初始化实例。
        """
        self._store = AgentSessionStore(directory=session_store_directory)

    @property
    def directory(self) -> Path:
        """返回会话存储目录（用于向后兼容性）。"""
        return self._store.directory

    def save_session(self, snapshot: AgentSessionSnapshot) -> Path:
        """保存会话快照到磁盘。

        Args:
            snapshot (AgentSessionSnapshot): 待保存的会话快照对象。
        Returns:
            Path: 实际写入的会话文件路径。
        Raises:
            ValueError: 当会话 ID 非法时抛出。
        """
        return self._store.save(snapshot)

    def load_session(self, session_id: str) -> AgentSessionSnapshot:
        """按 session_id 从磁盘恢复会话快照。

        Args:
            session_id (str): 需要加载的会话唯一标识。
        Returns:
            AgentSessionSnapshot: 从磁盘恢复出的会话快照对象。
        Raises:
            ValueError: 当会话文件不存在、内容损坏或格式不符合要求时抛出。
        """
        return self._store.load(session_id)

    def create_session_state(self, prompt: str) -> AgentSessionState:
        """为新会话创建运行时状态。

        Args:
            prompt (str): 用户发起本轮会话时输入的首条提示词。
        Returns:
            AgentSessionState: 已写入首条用户消息的会话状态对象。
        """
        return AgentSessionState.create(prompt)

    def restore_session_state(
        self,
        messages: list[dict],
        transcript: list[dict],
    ) -> AgentSessionState:
        """从已持久化的数据恢复运行时会话状态。

        若历史 transcript 为空，则使用 messages 生成最小可审计条目作为回退，
        保证恢复后的会话仍具备连续的转录视图。

        Args:
            messages (list[dict]): 恢复时使用的历史消息列表。
            transcript (list[dict]): 已持久化的历史转录条目。
        Returns:
            AgentSessionState: 从持久化数据恢复出的运行态会话对象。
        """
        return AgentSessionState.from_persisted(messages, transcript)


# 导出稳定的数据契约，作为 SessionManager 的公开契约
__all__ = [
    'SessionManager',
    'AgentSessionSnapshot',
    'AgentSessionState',
]
