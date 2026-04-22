"""ISSUE-007 会话持久化与基础恢复。"""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path

from .session_contracts import StoredAgentSession


DEFAULT_AGENT_SESSION_DIR = (Path('.port_sessions') / 'agent').resolve()


def save_agent_session(
    session: StoredAgentSession,
    directory: Path | None = None,
) -> Path:
    """把会话快照保存为 UTF-8 JSON 文件。"""
    path = _session_file_path(session.session_id, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    return path


def load_agent_session(
    session_id: str,
    directory: Path | None = None,
) -> StoredAgentSession:
    """按 session_id 读取并恢复会话快照。"""
    path = _session_file_path(session_id, directory=directory)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except JSONDecodeError as exc:
        raise ValueError(f'Corrupted session file: {path}') from exc

    if not isinstance(payload, dict):
        raise ValueError(f'Corrupted session file: {path}')

    session = StoredAgentSession.from_dict(payload)
    if session.session_id != _normalize_session_id(session_id):
        raise ValueError(f'Session id mismatch in session file: {path}')
    return session


def _session_file_path(
    session_id: str,
    *,
    directory: Path | None = None,
) -> Path:
    target_dir = (directory or DEFAULT_AGENT_SESSION_DIR).resolve()
    normalized_id = _normalize_session_id(session_id)
    return target_dir / f'{normalized_id}.json'


def _normalize_session_id(session_id: str) -> str:
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
