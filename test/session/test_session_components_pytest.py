"""Session internal component pytest tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core_contracts.config import (
    BudgetConfig,
    ContextPolicy,
    ExecutionPolicy,
    SessionPaths,
    ToolPermissionPolicy,
    WorkspaceScope,
)
from core_contracts.model import ModelConfig
from core_contracts.primitives import TokenUsage
from core_contracts.session_contracts import AgentSessionSnapshot, SessionPersistenceError, SessionValidationError
from session.session_id_policy import SessionIdPolicy
from session.session_snapshot_codec import SessionSnapshotCodec


def _snapshot(session_id: str = 'codec-01') -> AgentSessionSnapshot:
    """Build a minimal valid snapshot for codec tests.
    Args:
        session_id (str): Session identifier.
    Returns:
        AgentSessionSnapshot: Snapshot fixture.
    Raises:
        None
    """
    cwd = Path.cwd()
    return AgentSessionSnapshot(
        session_id=session_id,
        model_config=ModelConfig(model='demo-model'),
        workspace_scope=WorkspaceScope(cwd=cwd),
        execution_policy=ExecutionPolicy(),
        context_policy=ContextPolicy(),
        permissions=ToolPermissionPolicy(),
        budget_config=BudgetConfig(),
        session_paths=SessionPaths(),
        messages=({'role': 'user', 'content': 'hello'},),
        usage=TokenUsage(),
    )


def test_session_id_policy_normalize_rejects_path_escape() -> None:
    """SessionIdPolicy should reject unsafe identifiers.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When identifier validation regresses.
    """
    policy = SessionIdPolicy()

    with pytest.raises(SessionValidationError):
        policy.normalize('../escape')

    with pytest.raises(SessionValidationError):
        policy.normalize('a/b')


@pytest.mark.parametrize('raw, expected', [('  abc  ', 'abc'), ('session-1', 'session-1')])
def test_session_id_policy_normalize_accepts_valid_ids(raw: str, expected: str) -> None:
    """SessionIdPolicy should normalize valid identifiers.
    Args:
        raw (str): Raw session id.
        expected (str): Expected normalized id.
    Returns:
        None
    Raises:
        AssertionError: When normalization behavior regresses.
    """
    assert SessionIdPolicy().normalize(raw) == expected


def test_session_snapshot_codec_round_trip() -> None:
    """SessionSnapshotCodec should support stable encode/decode round-trip.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When codec behavior regresses.
    """
    codec = SessionSnapshotCodec()
    original = _snapshot('codec-round-trip')

    payload = codec.encode(original)
    restored = codec.decode(payload)

    assert restored.session_id == original.session_id
    assert restored.messages == original.messages


def test_session_snapshot_codec_decode_invalid_json_raises_persistence_error() -> None:
    """SessionSnapshotCodec should raise SessionPersistenceError for broken JSON.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When codec exception behavior regresses.
    """
    codec = SessionSnapshotCodec()

    with pytest.raises(SessionPersistenceError):
        codec.decode('{not-json')
