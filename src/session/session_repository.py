"""session 快照文件仓储。"""

from __future__ import annotations

from pathlib import Path

from core_contracts.session_contracts import SessionNotFoundError
from session.session_id_policy import SessionIdPolicy


DEFAULT_AGENT_SESSION_DIR = (Path('.port_sessions') / 'agent').resolve()  # Path：默认会话快照目录。


class SessionFileRepository:
    """负责会话快照文件的读写与路径管理。"""

    def __init__(self, directory: Path | None = None, id_policy: SessionIdPolicy | None = None) -> None:
        """初始化文件仓储。
        Args:
            directory (Path | None): 自定义会话目录；None 时使用默认目录。
            id_policy (SessionIdPolicy | None): 会话 ID 规范化策略。
        Returns:
            None
        """
        self.directory = (directory or DEFAULT_AGENT_SESSION_DIR).resolve()
        # Path：仓储根目录。
        self._id_policy = id_policy or SessionIdPolicy()
        # SessionIdPolicy：会话 ID 校验与规范化策略。

    def save_text(self, session_id: str, payload_text: str) -> Path:
        """按 session_id 写入快照文本。
        Args:
            session_id (str): 会话唯一标识。
            payload_text (str): 已编码的 JSON 文本。
        Returns:
            Path: 写入文件路径。
        Raises:
            SessionValidationError: session_id 不合法时由策略抛出。
        """
        file_path = self._session_file_path(session_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(payload_text, encoding='utf-8')
        return file_path

    def load_text(self, session_id: str) -> str:
        """按 session_id 读取快照文本。
        Args:
            session_id (str): 会话唯一标识。
        Returns:
            str: 读取到的 JSON 文本。
        Raises:
            SessionValidationError: session_id 不合法时由策略抛出。
            SessionNotFoundError: 会话文件不存在时抛出。
        """
        file_path = self._session_file_path(session_id)
        try:
            return file_path.read_text(encoding='utf-8')
        except FileNotFoundError as exc:
            raise SessionNotFoundError(f'Session not found: {file_path}') from exc

    def _session_file_path(self, session_id: str) -> Path:
        """根据 session_id 计算会话文件路径。
        Args:
            session_id (str): 会话唯一标识。
        Returns:
            Path: 会话文件路径。
        Raises:
            SessionValidationError: session_id 非法时由策略抛出。
        """
        normalized_id = self._id_policy.normalize(session_id)
        return self.directory / f'{normalized_id}.json'
