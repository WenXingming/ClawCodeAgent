"""SessionGateway pytest tests with strict dependency isolation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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
from core_contracts.session_contracts import (
    AgentSessionSnapshot,
    AgentSessionState,
    SessionLoadRequest,
    SessionPersistenceError,
    SessionSaveRequest,
    SessionStateCreateRequest,
    SessionStateResumeRequest,
    SessionValidationError,
)
from session.session_gateway import SessionGateway


def _snapshot(session_id: str = 's-001') -> AgentSessionSnapshot:
    """Build a minimal valid snapshot for gateway tests.
    Args:
        session_id (str): Session identifier.
    Returns:
        AgentSessionSnapshot: Snapshot test fixture.
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


def test_save_contract_returns_save_result_with_path() -> None:
    """Contract save API should return SessionSaveResult with stable fields.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When save orchestration behavior regresses.
    """
    repository = MagicMock()
    codec = MagicMock()
    state_factory = MagicMock()
    store = MagicMock()

    snap = _snapshot('save-01')
    codec.encode.return_value = '{"session_id":"save-01"}'
    repository.save_text.return_value = Path('D:/tmp/save-01.json')

    gateway = SessionGateway(
        repository=repository,
        snapshot_codec=codec,
        state_factory=state_factory,
        store=store,
    )

    result = gateway.save(SessionSaveRequest(snapshot=snap))

    assert result.session_id == 'save-01'
    assert result.session_path.endswith('save-01.json')
    codec.encode.assert_called_once_with(snap)
    repository.save_text.assert_called_once_with('save-01', '{"session_id":"save-01"}')


def test_load_contract_returns_snapshot() -> None:
    """Contract load API should decode payload and return snapshot result.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When load orchestration behavior regresses.
    """
    repository = MagicMock()
    repository._id_policy.normalize.return_value = 'load-01'
    repository.load_text.return_value = '{"session_id":"load-01"}'
    codec = MagicMock()
    codec.decode.return_value = _snapshot('load-01')

    gateway = SessionGateway(repository=repository, snapshot_codec=codec, state_factory=MagicMock(), store=MagicMock())

    result = gateway.load(SessionLoadRequest(session_id=' load-01 '))

    assert result.session_id == 'load-01'
    assert result.snapshot.session_id == 'load-01'
    repository._id_policy.normalize.assert_called_once_with(' load-01 ')
    repository.load_text.assert_called_once_with('load-01')


def test_load_contract_raises_validation_error_on_id_mismatch() -> None:
    """Contract load API should fail fast when payload id mismatches request id.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When mismatch validation behavior regresses.
    """
    repository = MagicMock()
    repository._id_policy.normalize.return_value = 'load-02'
    repository.load_text.return_value = '{"session_id":"other"}'
    codec = MagicMock()
    codec.decode.return_value = _snapshot('other')

    gateway = SessionGateway(repository=repository, snapshot_codec=codec, state_factory=MagicMock(), store=MagicMock())

    with pytest.raises(SessionValidationError):
        gateway.load(SessionLoadRequest(session_id='load-02'))


def test_legacy_save_session_translates_contract_error_to_value_error() -> None:
    """Legacy API should preserve ValueError contract for callers.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When legacy exception translation regresses.
    """
    repository = MagicMock()
    repository.save_text.side_effect = SessionPersistenceError('write failed')
    codec = MagicMock()
    codec.encode.return_value = '{}'

    gateway = SessionGateway(repository=repository, snapshot_codec=codec, state_factory=MagicMock(), store=MagicMock())

    with pytest.raises(ValueError):
        gateway.save_session(_snapshot('legacy-save'))


def test_legacy_load_session_translates_contract_error_to_value_error() -> None:
    """Legacy API should preserve ValueError contract for not-found or parse errors.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When legacy exception translation regresses.
    """
    repository = MagicMock()
    repository._id_policy.normalize.return_value = 'legacy-load'
    repository.load_text.side_effect = SessionPersistenceError('broken')

    gateway = SessionGateway(repository=repository, snapshot_codec=MagicMock(), state_factory=MagicMock(), store=MagicMock())

    with pytest.raises(ValueError):
        gateway.load_session('legacy-load')


def test_create_and_resume_state_delegate_to_state_factory() -> None:
    """Gateway state APIs should delegate to SessionStateFactory.
    Args:
        None
    Returns:
        None
    Raises:
        AssertionError: When state factory delegation regresses.
    """
    factory = MagicMock()
    state_obj = AgentSessionState.create('seed')
    factory.create.return_value = state_obj
    factory.resume.return_value = state_obj

    gateway = SessionGateway(repository=MagicMock(), snapshot_codec=MagicMock(), state_factory=factory, store=MagicMock())

    created = gateway.create_state(SessionStateCreateRequest(prompt='hello'))
    resumed = gateway.resume_state(
        SessionStateResumeRequest(messages=({'role': 'user', 'content': 'u'},), transcript=({'role': 'assistant', 'content': 'a'},))
    )

    assert created is state_obj
    assert resumed is state_obj
    factory.create.assert_called_once_with('hello')
    factory.resume.assert_called_once()
