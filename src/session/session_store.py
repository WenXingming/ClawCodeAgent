"""提供代理会话快照的基础持久化与恢复能力。

本模块只承担最小的会话存取职责：
1. 把 `AgentSessionSnapshot` 写入 UTF-8 JSON 文件。
2. 按 `session_id` 从磁盘恢复已保存的会话快照。
3. 对会话文件名做基础校验，避免路径逃逸与损坏数据污染。

模块内部按公开入口到私有辅助函数的顺序组织，便于顺着存取链路阅读。
"""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path

from .session_snapshot import AgentSessionSnapshot


class AgentSessionStore:
    """负责代理会话快照的文件持久化与恢复。

    该类被运行时用作最薄的一层文件存储适配器：外部只需传入快照对象或 `session_id`，即可完成保存与恢复。类内私有方法专门负责路径计算与 `session_id` 规范化，避免调用方重复处理文件系统细节。
    """

    DEFAULT_AGENT_SESSION_DIR = (Path('.port_sessions') / 'agent').resolve()  # Path：默认的会话快照目录绝对路径。

    def __init__(self, directory: Path | None = None) -> None:
        """初始化会话快照存储器。

        Args:
            directory (Path | None): 自定义的会话目录；为 None 时使用默认目录。
        Returns:
            None: 该方法初始化实例并解析最终目录路径。
        """
        self.directory = (directory or self.DEFAULT_AGENT_SESSION_DIR).resolve()
        # Path：当前实例实际使用的会话快照根目录。

    def save(self, session_snapshot: AgentSessionSnapshot) -> Path:
        """把会话快照保存为 UTF-8 JSON 文件。

        Args:
            session_snapshot (AgentSessionSnapshot): 待写入磁盘的会话快照对象。
        Returns:
            Path: 实际写入的 JSON 文件绝对路径。
        Raises:
            ValueError: 当 `session_snapshot.session_id` 非法时，由路径辅助函数抛出。
        """
        path = self._session_file_path(session_snapshot.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(session_snapshot.to_dict(), indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
        return path

    def load(self, session_id: str) -> AgentSessionSnapshot:
        """按 session_id 读取并恢复会话快照。

        Args:
            session_id (str): 需要加载的会话唯一标识。
        Returns:
            AgentSessionSnapshot: 从 JSON 文件恢复出的会话快照对象。
        Raises:
            ValueError: 当目标文件不存在、JSON 内容损坏、顶层结构不是对象，或
                文件内 session_id 与请求值不一致时抛出。
        """
        path = self._session_file_path(session_id)
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError as exc:
            raise ValueError(f'Session not found: {path}') from exc
        except JSONDecodeError as exc:
            raise ValueError(f'Corrupted session file: {path}') from exc

        if not isinstance(payload, dict):
            raise ValueError(f'Corrupted session file: {path}')

        session_snapshot = AgentSessionSnapshot.from_dict(payload)
        if session_snapshot.session_id != self._normalize_session_id(session_id):
            raise ValueError(f'Session id mismatch in session file: {path}')
        return session_snapshot

    def _session_file_path(self, session_id: str) -> Path:
        """根据 session_id 计算目标会话文件路径。

        Args:
            session_id (str): 会话唯一标识。
        Returns:
            Path: 目标会话文件路径。
        Raises:
            ValueError: 当 `session_id` 无法通过规范化校验时抛出。
        """
        normalized_id = self._normalize_session_id(session_id)
        return self.directory / f'{normalized_id}.json'

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        """规范化并校验 session_id。

        该函数禁止空值、路径分隔符以及可能导致路径逃逸的名字，以确保会话文件始终落在目标目录内部。

        Args:
            session_id (str): 原始会话标识。
        Returns:
            str: 去除首尾空白后的合法 session_id。
        Raises:
            ValueError: 当 session_id 不是字符串、为空或包含非法路径成分时抛出。
        """
        if not isinstance(session_id, str):
            raise ValueError('session_id must be a string')

        normalized = session_id.strip()
        if not normalized:
            raise ValueError('session_id must not be empty')

        candidate = Path(normalized)
        if candidate.name != normalized or normalized in {'.', '..'}:
            raise ValueError(f'Invalid session_id: {session_id!r}')

        if any(separator in normalized for separator in ('/', '\\')):
            raise ValueError(f'Invalid session_id: {session_id!r}')

        return normalized
