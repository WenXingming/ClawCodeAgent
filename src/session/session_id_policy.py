"""session_id 规范化与校验策略。"""

from __future__ import annotations

from pathlib import Path

from core_contracts.session_contracts import SessionValidationError


class SessionIdPolicy:
    """负责 session_id 的规范化与安全校验。"""

    def normalize(self, session_id: str) -> str:
        """规范化并校验 session_id。
        Args:
            session_id (str): 原始会话标识。
        Returns:
            str: 去除首尾空白后的合法 session_id。
        Raises:
            SessionValidationError: 当 session_id 非字符串、为空或包含非法路径成分时抛出。
        """
        if not isinstance(session_id, str):
            raise SessionValidationError('session_id must be a string')

        normalized = session_id.strip()
        if not normalized:
            raise SessionValidationError('session_id must not be empty')

        candidate = Path(normalized)
        if candidate.name != normalized or normalized in {'.', '..'}:
            raise SessionValidationError(f'Invalid session_id: {session_id!r}')

        if any(separator in normalized for separator in ('/', '\\')):
            raise SessionValidationError(f'Invalid session_id: {session_id!r}')

        return normalized
