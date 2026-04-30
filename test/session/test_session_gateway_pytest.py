"""SessionGateway 单元测试。

测试策略：
- 通过显式注入，Mock SessionStore 和 SessionStateRuntime，
  严格隔离所有外部依赖。
- 覆盖四个标准契约方法（save / load / create_state / resume_state）的主流程与异常路径。
- 覆盖 directory 属性委托。
"""

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
    SessionContractError,
    SessionLoadRequest,
    SessionNotFoundError,
    SessionPersistenceError,
    SessionSaveRequest,
    SessionStateCreateRequest,
    SessionStateResumeRequest,
    SessionValidationError,
)
from session.session_gateway import SessionGateway


# ── 测试固件工厂 ─────────────────────────────────────────────────────────────

def _make_snapshot(session_id: str = 's-001') -> AgentSessionSnapshot:
    """构造最小合法的 AgentSessionSnapshot 测试固件。

    Args:
        session_id (str): 会话标识。
    Returns:
        AgentSessionSnapshot: 填充了所有必填字段的快照对象。
    Raises:
        None
    """
    return AgentSessionSnapshot(
        session_id=session_id,
        model_config=ModelConfig(model='test-model'),
        workspace_scope=WorkspaceScope(cwd=Path.cwd()),
        execution_policy=ExecutionPolicy(),
        context_policy=ContextPolicy(),
        permissions=ToolPermissionPolicy(),
        budget_config=BudgetConfig(),
        session_paths=SessionPaths(),
        messages=({'role': 'user', 'content': 'hello'},),
        usage=TokenUsage(),
    )


def _make_gateway_with_mocks() -> tuple[SessionGateway, MagicMock, MagicMock]:
    """构造带有 Mock 内部组件的 SessionGateway。

    通过显式注入替换 SessionStore 与 SessionStateRuntime 的真实实现。

    Args:
        None
    Returns:
        tuple: (gateway, mock_serializer, mock_state_builder)
    Raises:
        None
    """
    mock_store = MagicMock()
    mock_state = MagicMock()
    mock_store.directory = Path('/tmp/sessions')
    gateway = SessionGateway(session_store=mock_store, session_state=mock_state)
    return gateway, mock_store, mock_state


# ── directory 属性 ───────────────────────────────────────────────────────────

class TestDirectoryProperty:
    """验证 directory 属性委托给 SessionStore。"""

    def test_directory_delegates_to_serializer(self) -> None:
        """directory 属性应返回 serializer.directory 的值。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        mock_store.directory = Path('/tmp/sessions')
        assert gateway.directory == Path('/tmp/sessions')


# ── save ─────────────────────────────────────────────────────────────────────

class TestSave:
    """验证 save 方法的主流程与异常翻译。"""

    def test_save_returns_save_result_on_success(self) -> None:
        """save 成功时应返回含 session_id 与 session_path 的 SessionSaveResult。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        snapshot = _make_snapshot('s-save-01')
        mock_store.save.return_value = Path('/tmp/sessions/s-save-01.json')

        result = gateway.save(SessionSaveRequest(snapshot=snapshot))

        assert result.session_id == 's-save-01'
        assert Path(result.session_path) == Path('/tmp/sessions/s-save-01.json')
        mock_store.save.assert_called_once_with(snapshot)

    def test_save_re_raises_session_validation_error_unchanged(self) -> None:
        """save 应原样透传 SessionValidationError（属于 SessionContractError 子类）。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        mock_store.save.side_effect = SessionValidationError('bad id')

        with pytest.raises(SessionValidationError, match='bad id'):
            gateway.save(SessionSaveRequest(snapshot=_make_snapshot()))

    def test_save_re_raises_session_persistence_error_unchanged(self) -> None:
        """save 应原样透传 SessionPersistenceError。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        mock_store.save.side_effect = SessionPersistenceError('disk full')

        with pytest.raises(SessionPersistenceError, match='disk full'):
            gateway.save(SessionSaveRequest(snapshot=_make_snapshot()))

    def test_save_wraps_unexpected_exception_as_session_contract_error(self) -> None:
        """save 应将未预期异常包装为 SessionContractError。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        mock_store.save.side_effect = RuntimeError('unexpected')

        with pytest.raises(SessionContractError, match='unexpected'):
            gateway.save(SessionSaveRequest(snapshot=_make_snapshot()))


# ── load ─────────────────────────────────────────────────────────────────────

class TestLoad:
    """验证 load 方法的主流程与异常翻译。"""

    def test_load_returns_load_result_on_success(self) -> None:
        """load 成功时应返回含 session_id 与快照的 SessionLoadResult。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        snapshot = _make_snapshot('s-load-01')
        mock_store.load.return_value = snapshot

        result = gateway.load(SessionLoadRequest(session_id='s-load-01'))

        assert result.session_id == 's-load-01'
        assert result.snapshot is snapshot
        mock_store.load.assert_called_once_with('s-load-01')

    def test_load_re_raises_session_not_found_error(self) -> None:
        """load 应原样透传 SessionNotFoundError。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        mock_store.load.side_effect = SessionNotFoundError('no file')

        with pytest.raises(SessionNotFoundError, match='no file'):
            gateway.load(SessionLoadRequest(session_id='missing'))

    def test_load_re_raises_session_persistence_error(self) -> None:
        """load 应原样透传 SessionPersistenceError。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        mock_store.load.side_effect = SessionPersistenceError('corrupt')

        with pytest.raises(SessionPersistenceError, match='corrupt'):
            gateway.load(SessionLoadRequest(session_id='corrupt-id'))

    def test_load_wraps_unexpected_exception(self) -> None:
        """load 应将未预期异常包装为 SessionContractError。"""
        gateway, mock_store, _ = _make_gateway_with_mocks()
        mock_store.load.side_effect = OSError('io error')

        with pytest.raises(SessionContractError, match='io error'):
            gateway.load(SessionLoadRequest(session_id='any'))


# ── create_state ─────────────────────────────────────────────────────────────

class TestCreateState:
    """验证 create_state 方法的主流程与异常翻译。"""

    def test_create_state_returns_agent_session_state(self) -> None:
        """create_state 成功时应返回 AgentSessionState。"""
        gateway, _, mock_builder = _make_gateway_with_mocks()
        expected_state = MagicMock(spec=AgentSessionState)
        mock_builder.build_new.return_value = expected_state

        result = gateway.create_state(SessionStateCreateRequest(prompt='hello'))

        assert result is expected_state
        mock_builder.build_new.assert_called_once_with('hello')

    def test_create_state_re_raises_session_validation_error(self) -> None:
        """create_state 应原样透传 SessionValidationError。"""
        gateway, _, mock_builder = _make_gateway_with_mocks()
        mock_builder.build_new.side_effect = SessionValidationError('empty prompt')

        with pytest.raises(SessionValidationError, match='empty prompt'):
            gateway.create_state(SessionStateCreateRequest(prompt=''))

    def test_create_state_wraps_unexpected_exception(self) -> None:
        """create_state 应将未预期异常包装为 SessionContractError。"""
        gateway, _, mock_builder = _make_gateway_with_mocks()
        mock_builder.build_new.side_effect = RuntimeError('unexpected')

        with pytest.raises(SessionContractError, match='unexpected'):
            gateway.create_state(SessionStateCreateRequest(prompt='hi'))


# ── resume_state ─────────────────────────────────────────────────────────────

class TestResumeState:
    """验证 resume_state 方法的主流程与异常翻译。"""

    def test_resume_state_returns_agent_session_state(self) -> None:
        """resume_state 成功时应返回 AgentSessionState。"""
        gateway, _, mock_builder = _make_gateway_with_mocks()
        expected_state = MagicMock(spec=AgentSessionState)
        mock_builder.build_from_persisted.return_value = expected_state
        messages: tuple = ({'role': 'user', 'content': 'hi'},)
        transcript: tuple = ({'role': 'user', 'content': 'hi'},)

        result = gateway.resume_state(
            SessionStateResumeRequest(messages=messages, transcript=transcript)
        )

        assert result is expected_state
        mock_builder.build_from_persisted.assert_called_once_with(messages, transcript)

    def test_resume_state_re_raises_session_validation_error(self) -> None:
        """resume_state 应原样透传 SessionValidationError。"""
        gateway, _, mock_builder = _make_gateway_with_mocks()
        mock_builder.build_from_persisted.side_effect = SessionValidationError('bad msg')

        with pytest.raises(SessionValidationError, match='bad msg'):
            gateway.resume_state(
                SessionStateResumeRequest(messages=('bad',), transcript=())
            )

    def test_resume_state_wraps_unexpected_exception(self) -> None:
        """resume_state 应将未预期异常包装为 SessionContractError。"""
        gateway, _, mock_builder = _make_gateway_with_mocks()
        mock_builder.build_from_persisted.side_effect = TypeError('bad type')

        with pytest.raises(SessionContractError, match='bad type'):
            gateway.resume_state(SessionStateResumeRequest(messages=(), transcript=()))


