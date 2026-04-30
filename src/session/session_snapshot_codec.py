"""会话快照 JSON 编解码器。"""

from __future__ import annotations

import json
from json import JSONDecodeError

from core_contracts.primitives import JSONDict
from core_contracts.session_contracts import AgentSessionSnapshot, SessionPersistenceError


class SessionSnapshotCodec:
    """负责 AgentSessionSnapshot 与 JSON 文本的互转。"""

    def encode(self, snapshot: AgentSessionSnapshot) -> str:
        """将会话快照编码为 JSON 文本。
        Args:
            snapshot (AgentSessionSnapshot): 待编码快照。
        Returns:
            str: UTF-8 JSON 文本。
        Raises:
            SessionPersistenceError: 编码失败时抛出。
        """
        try:
            return json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False)
        except Exception as exc:
            raise SessionPersistenceError(f'Failed to encode session snapshot: {exc}') from exc

    def decode(self, payload_text: str) -> AgentSessionSnapshot:
        """将 JSON 文本解码为会话快照。
        Args:
            payload_text (str): JSON 文本。
        Returns:
            AgentSessionSnapshot: 恢复后的快照对象。
        Raises:
            SessionPersistenceError: JSON 损坏或契约反序列化失败时抛出。
        """
        payload = self._parse_json_payload(payload_text)
        try:
            return AgentSessionSnapshot.from_dict(payload)
        except Exception as exc:
            raise SessionPersistenceError(f'Failed to parse session snapshot payload: {exc}') from exc

    def _parse_json_payload(self, payload_text: str) -> JSONDict:
        """将原始 JSON 文本解析为对象字典。
        Args:
            payload_text (str): 原始 JSON 文本。
        Returns:
            JSONDict: 顶层对象字典。
        Raises:
            SessionPersistenceError: 文本不是合法 JSON 或顶层结构不是对象时抛出。
        """
        try:
            payload = json.loads(payload_text)
        except JSONDecodeError as exc:
            raise SessionPersistenceError('Corrupted session file JSON content') from exc

        if not isinstance(payload, dict):
            raise SessionPersistenceError('Corrupted session file: top-level JSON object required')

        return payload
