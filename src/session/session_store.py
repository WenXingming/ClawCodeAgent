"""会话快照存储组件。

SessionStore 是 session 模块唯一的磁盘 I/O 职责承载者，整合了四项高度关联的职责：
  1. session_id 校验与路径安全检查
  2. 快照文件路径解析
  3. UTF-8 JSON 文件读写
  4. AgentSessionSnapshot 与 JSON 文本之间的编解码

通过职责聚合消除了原有 SessionIdPolicy + SessionFileRepository + SessionSnapshotCodec
三层轻薄类的碎片化结构，同时保持对外接口极简：save / load。
"""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path

from core_contracts.session_contracts import (
    AgentSessionSnapshot,
    SessionNotFoundError,
    SessionPersistenceError,
    SessionValidationError,
)

# Path: session 快照的默认存储根目录（相对于进程工作目录）。
DEFAULT_SESSION_DIR: Path = (Path('.port_sessions') / 'agent').resolve()


class SessionStore:
    """会话快照持久化存储组件。

    核心职责：
    - 校验并规范化 session_id，拒绝路径遍历攻击
    - 将 AgentSessionSnapshot 编码为缩进 JSON 并写入磁盘
    - 从磁盘读取 JSON 并反序列化还原为 AgentSessionSnapshot
    - 校验文件中 session_id 与请求 ID 的一致性
    """

    def __init__(self, directory: Path | None = None) -> None:
        """初始化存储组件，解析快照存储根目录。

        Args:
            directory (Path | None): 快照存储根目录；None 时使用 DEFAULT_SESSION_DIR。
        Returns:
            None
        Raises:
            None
        """
        self.directory: Path = (directory or DEFAULT_SESSION_DIR).resolve()
        # Path: 快照文件的存储根目录（绝对路径，保证跨平台一致性）。

    # ── 公有接口 ──────────────────────────────────────────────────────────────

    def save(self, snapshot: AgentSessionSnapshot) -> Path:
        """将快照序列化并持久化到磁盘。

        Args:
            snapshot (AgentSessionSnapshot): 待持久化的会话快照对象。
        Returns:
            Path: 写入成功后的目标文件绝对路径。
        Raises:
            SessionValidationError: session_id 不合法时抛出。
            SessionPersistenceError: JSON 编码或文件写入失败时抛出。
        """
        validated_id = self._validate_id(snapshot.session_id)
        payload = self._encode(snapshot)
        return self._write_file(validated_id, payload)

    def load(self, session_id: str) -> AgentSessionSnapshot:
        """从磁盘读取快照文件并反序列化。

        Args:
            session_id (str): 会话唯一标识。
        Returns:
            AgentSessionSnapshot: 反序列化后的快照对象。
        Raises:
            SessionValidationError: session_id 不合法时抛出。
            SessionNotFoundError: 快照文件不存在时抛出。
            SessionPersistenceError: 文件损坏或反序列化失败时抛出。
        """
        validated_id = self._validate_id(session_id)
        payload = self._read_file(validated_id)
        snapshot = self._decode(payload)
        if snapshot.session_id != validated_id:
            file_path = self._file_path(validated_id)
            raise SessionPersistenceError(
                f'session_id 不一致：文件 {file_path} 内存储的 ID'
                f' "{snapshot.session_id}" 与请求的 ID "{validated_id}" 不匹配'
            )
        return snapshot

    # ── 私有辅助（深度优先顺序：_validate_id → _encode → _write_file
    #                              ↘ _read_file → _decode → _parse_json → _file_path）

    def _validate_id(self, session_id: str) -> str:
        """规范化并校验 session_id 的合法性与路径安全性。

        Args:
            session_id (str): 原始会话标识。
        Returns:
            str: 去除首尾空白后的合法 session_id。
        Raises:
            SessionValidationError: 非字符串、空值、含路径分隔符或为保留名时抛出。
        """
        if not isinstance(session_id, str):
            raise SessionValidationError('session_id 必须为字符串')
        normalized = session_id.strip()
        if not normalized:
            raise SessionValidationError('session_id 不能为空白字符串')
        if normalized in {'.', '..'}:
            raise SessionValidationError(f'非法 session_id: {session_id!r}')
        if any(sep in normalized for sep in ('/', '\\')):
            raise SessionValidationError(f'session_id 不得包含路径分隔符: {session_id!r}')
        if Path(normalized).name != normalized:
            raise SessionValidationError(f'非法 session_id: {session_id!r}')
        return normalized

    def _encode(self, snapshot: AgentSessionSnapshot) -> str:
        """将快照对象序列化为缩进 JSON 字符串。

        Args:
            snapshot (AgentSessionSnapshot): 待序列化的快照对象。
        Returns:
            str: UTF-8 兼容的缩进 JSON 文本。
        Raises:
            SessionPersistenceError: 序列化过程中发生异常时抛出。
        """
        try:
            return json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False)
        except Exception as exc:
            raise SessionPersistenceError(f'快照序列化失败: {exc}') from exc

    def _write_file(self, session_id: str, payload: str) -> Path:
        """将 JSON 文本写入对应的快照文件，必要时自动创建父目录。

        Args:
            session_id (str): 已校验的会话标识。
            payload (str): 待写入的 JSON 文本。
        Returns:
            Path: 写入成功后的目标文件绝对路径。
        Raises:
            SessionPersistenceError: 目录创建或文件写入失败时抛出。
        """
        file_path = self._file_path(session_id)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(payload, encoding='utf-8')
        except OSError as exc:
            raise SessionPersistenceError(f'写入快照文件失败 {file_path}: {exc}') from exc
        return file_path

    def _read_file(self, session_id: str) -> str:
        """从对应的快照文件读取 JSON 文本。

        Args:
            session_id (str): 已校验的会话标识。
        Returns:
            str: 文件中的 UTF-8 文本内容。
        Raises:
            SessionNotFoundError: 文件不存在时抛出。
            SessionPersistenceError: 文件读取 I/O 失败时抛出。
        """
        file_path = self._file_path(session_id)
        try:
            return file_path.read_text(encoding='utf-8')
        except FileNotFoundError as exc:
            raise SessionNotFoundError(f'会话快照文件不存在: {file_path}') from exc
        except OSError as exc:
            raise SessionPersistenceError(f'读取快照文件失败 {file_path}: {exc}') from exc

    def _decode(self, payload: str) -> AgentSessionSnapshot:
        """将 JSON 字符串反序列化为快照对象。

        Args:
            payload (str): 从磁盘读取的 JSON 文本。
        Returns:
            AgentSessionSnapshot: 反序列化后的快照对象。
        Raises:
            SessionPersistenceError: JSON 损坏或契约字段不合法时抛出。
        """
        raw = self._parse_json(payload)
        try:
            return AgentSessionSnapshot.from_dict(raw)
        except Exception as exc:
            raise SessionPersistenceError(f'快照载荷反序列化失败: {exc}') from exc

    def _parse_json(self, payload: str) -> dict:
        """将 JSON 文本解析为原始字典，校验顶层结构为对象。

        Args:
            payload (str): 待解析的 JSON 字符串。
        Returns:
            dict: 解析后的顶层对象字典。
        Raises:
            SessionPersistenceError: JSON 格式错误或顶层结构非对象时抛出。
        """
        try:
            data = json.loads(payload)
        except JSONDecodeError as exc:
            raise SessionPersistenceError('快照文件 JSON 内容损坏') from exc
        if not isinstance(data, dict):
            raise SessionPersistenceError('快照文件格式错误：顶层结构必须为 JSON 对象')
        return data

    def _file_path(self, session_id: str) -> Path:
        """计算指定会话 ID 对应的快照文件绝对路径。

        Args:
            session_id (str): 已校验的会话标识。
        Returns:
            Path: 快照文件的绝对路径（directory / session_id.json）。
        Raises:
            None
        """
        return self.directory / f'{session_id}.json'
