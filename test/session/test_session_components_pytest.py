"""SessionStore 与 SessionStateRuntime 单元测试。

测试策略：
- SessionStore：使用 tmp_path 在真实临时文件系统上验证完整 I/O 链路；
  对路径校验和格式错误使用纯内存数据，不写磁盘。
- SessionStateRuntime：完全基于内存，无任何 I/O，测试创建与恢复的主流程和校验边界。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core_contracts.config import (
    BudgetConfig,
    ContextPolicy,
    ExecutionPolicy,
    SessionPaths,
    WorkspaceScope,
)
from core_contracts.model import ModelConfig
from core_contracts.primitives import TokenUsage
from core_contracts.session_contracts import (
    AgentSessionSnapshot,
    AgentSessionState,
    SessionNotFoundError,
    SessionPersistenceError,
    SessionValidationError,
)
from core_contracts.tools_contracts import ToolPermissionPolicy
from session.session_state import SessionStateRuntime
from session.session_store import SessionStore


# ── 共用固件工厂 ─────────────────────────────────────────────────────────────

def _make_snapshot(session_id: str = 'test-001', cwd: Path | None = None) -> AgentSessionSnapshot:
    """构造最小合法快照对象用于序列化测试。

    Args:
        session_id (str): 会话标识。
        cwd (Path | None): 工作区目录；None 时使用 Path.cwd()。
    Returns:
        AgentSessionSnapshot: 测试用快照固件。
    Raises:
        None
    """
    return AgentSessionSnapshot(
        session_id=session_id,
        model_config=ModelConfig(model='test-model'),
        workspace_scope=WorkspaceScope(cwd=cwd or Path.cwd()),
        execution_policy=ExecutionPolicy(),
        context_policy=ContextPolicy(),
        permissions=ToolPermissionPolicy(),
        budget_config=BudgetConfig(),
        session_paths=SessionPaths(),
        messages=({'role': 'user', 'content': 'hi'},),
        usage=TokenUsage(),
    )


class TestSessionStoreSaveLoad:
    """验证 SessionStore.save 和 load 的端到端 I/O 链路。"""

    def test_save_creates_json_file_at_expected_path(self, tmp_path: Path) -> None:
        """save 应在 directory/session_id.json 写入文件。"""
        serializer = SessionStore(tmp_path)
        serializer.save(_make_snapshot('abc-123'))
        assert (tmp_path / 'abc-123.json').exists()

    def test_save_produces_valid_json_content(self, tmp_path: Path) -> None:
        """save 写入的文件应为合法 JSON，且顶层包含 session_id 字段。"""
        serializer = SessionStore(tmp_path)
        serializer.save(_make_snapshot('json-check'))
        raw = json.loads((tmp_path / 'json-check.json').read_text(encoding='utf-8'))
        assert raw['session_id'] == 'json-check'

    def test_save_and_load_round_trip_preserves_all_fields(self, tmp_path: Path) -> None:
        """save 后 load 应还原出与原始快照相等的对象。"""
        serializer = SessionStore(tmp_path)
        snapshot = AgentSessionSnapshot(
            session_id='round-trip',
            model_config=ModelConfig(model='gpt-4'),
            workspace_scope=WorkspaceScope(cwd=tmp_path),
            execution_policy=ExecutionPolicy(max_turns=5),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            budget_config=BudgetConfig(),
            session_paths=SessionPaths(),
            messages=({'role': 'user', 'content': 'test'},),
            transcript=({'role': 'user', 'content': 'test'},),
            final_output='done',
            turns=1,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )
        serializer.save(snapshot)
        restored = serializer.load('round-trip')
        assert restored.session_id == snapshot.session_id
        assert restored.model_config == snapshot.model_config
        assert restored.messages == snapshot.messages
        assert restored.transcript == snapshot.transcript
        assert restored.final_output == snapshot.final_output
        assert restored.turns == snapshot.turns
        assert restored.usage == snapshot.usage

    def test_save_creates_parent_directory_automatically(self, tmp_path: Path) -> None:
        """save 应在父目录不存在时自动创建目录。"""
        nested_dir = tmp_path / 'level1' / 'level2'
        SessionStore(nested_dir).save(_make_snapshot('nested-id'))
        assert (nested_dir / 'nested-id.json').exists()

    def test_load_whitespace_session_id_is_normalized(self, tmp_path: Path) -> None:
        """load 传入带空白的 session_id 应规范化后正常加载。"""
        serializer = SessionStore(tmp_path)
        serializer.save(_make_snapshot('ws-trim'))
        restored = serializer.load('  ws-trim  ')
        assert restored.session_id == 'ws-trim'


class TestSessionStoreValidateId:
    """验证 _validate_id 对各类非法 session_id 的拒绝行为。"""

    def test_empty_string_raises_validation_error(self, tmp_path: Path) -> None:
        """空字符串 session_id 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='空白'):
            SessionStore(tmp_path).load('')

    def test_whitespace_only_raises_validation_error(self, tmp_path: Path) -> None:
        """纯空白字符串 session_id 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='空白'):
            SessionStore(tmp_path).load('   ')

    def test_non_string_raises_validation_error(self, tmp_path: Path) -> None:
        """非字符串类型 session_id 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='必须为字符串'):
            SessionStore(tmp_path).load(123)

    def test_slash_in_id_raises_validation_error(self, tmp_path: Path) -> None:
        """含正斜杠的 session_id 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='路径分隔符'):
            SessionStore(tmp_path).load('a/b')

    def test_backslash_in_id_raises_validation_error(self, tmp_path: Path) -> None:
        """含反斜杠的 session_id 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='路径分隔符'):
            SessionStore(tmp_path).load('a\\b')

    def test_dot_dot_raises_validation_error(self, tmp_path: Path) -> None:
        """.. 形式的 session_id 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError):
            SessionStore(tmp_path).load('..')

    def test_single_dot_raises_validation_error(self, tmp_path: Path) -> None:
        """. 形式的 session_id 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError):
            SessionStore(tmp_path).load('.')


class TestSessionStoreLoadErrors:
    """验证 load 对文件缺失与内容损坏的错误处理。"""

    def test_load_nonexistent_session_raises_not_found_error(self, tmp_path: Path) -> None:
        """加载不存在的会话文件应抛出 SessionNotFoundError。"""
        with pytest.raises(SessionNotFoundError):
            SessionStore(tmp_path).load('does-not-exist')

    def test_load_invalid_json_raises_persistence_error(self, tmp_path: Path) -> None:
        """加载 JSON 格式损坏的文件应抛出 SessionPersistenceError。"""
        (tmp_path / 'bad-json.json').write_text('NOT_JSON{{{', encoding='utf-8')
        with pytest.raises(SessionPersistenceError, match='损坏'):
            SessionStore(tmp_path).load('bad-json')

    def test_load_json_array_at_root_raises_persistence_error(self, tmp_path: Path) -> None:
        """顶层为 JSON 数组而非对象时应抛出 SessionPersistenceError。"""
        (tmp_path / 'bad-root.json').write_text('[1, 2, 3]', encoding='utf-8')
        with pytest.raises(SessionPersistenceError, match='顶层结构'):
            SessionStore(tmp_path).load('bad-root')

    def test_load_mismatched_session_id_raises_persistence_error(self, tmp_path: Path) -> None:
        """文件内 session_id 与请求 ID 不一致时应抛出 SessionPersistenceError。"""
        serializer = SessionStore(tmp_path)
        serializer.save(_make_snapshot('original'))
        (tmp_path / 'original.json').rename(tmp_path / 'renamed.json')
        with pytest.raises(SessionPersistenceError, match='不一致'):
            serializer.load('renamed')


class TestSessionStateRuntimeBuildNew:
    """验证 build_new 的主流程与校验边界。"""

    def test_build_new_returns_session_state_with_user_message(self) -> None:
        """build_new 应返回包含首条用户消息的 AgentSessionState。"""
        state = SessionStateRuntime().build_new('hello world')
        assert isinstance(state, AgentSessionState)
        assert any(
            m.get('role') == 'user' and m.get('content') == 'hello world'
            for m in state.messages
        )

    def test_build_new_empty_prompt_raises_validation_error(self) -> None:
        """空字符串 prompt 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='空白'):
            SessionStateRuntime().build_new('')

    def test_build_new_whitespace_only_prompt_raises_validation_error(self) -> None:
        """纯空白 prompt 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='空白'):
            SessionStateRuntime().build_new('   ')

    def test_build_new_non_string_prompt_raises_validation_error(self) -> None:
        """非字符串 prompt 应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='必须为字符串'):
            SessionStateRuntime().build_new(42)


class TestSessionStateRuntimeBuildFromPersisted:
    """验证 build_from_persisted 的主流程与校验边界。"""

    def test_build_from_persisted_restores_messages(self) -> None:
        """build_from_persisted 应还原完整的消息列表。"""
        messages = (
            {'role': 'user', 'content': 'hello'},
            {'role': 'assistant', 'content': 'world'},
        )
        transcript = (
            {'role': 'user', 'content': 'hello'},
        )
        state = SessionStateRuntime().build_from_persisted(messages, transcript)
        assert len(state.messages) == 2
        assert state.messages[0] == {'role': 'user', 'content': 'hello'}

    def test_build_from_persisted_does_not_share_reference_with_input(self) -> None:
        """build_from_persisted 应对输入数据进行深拷贝，防止外部修改污染状态。"""
        original_msg: dict = {'role': 'user', 'content': 'original'}
        state = SessionStateRuntime().build_from_persisted((original_msg,), ())
        original_msg['content'] = 'mutated'
        assert state.messages[0]['content'] == 'original'

    def test_build_from_persisted_empty_sequences_returns_state(self) -> None:
        """空消息和转录序列应成功返回有效状态对象。"""
        state = SessionStateRuntime().build_from_persisted((), ())
        assert isinstance(state, AgentSessionState)

    def test_build_from_persisted_non_dict_message_raises_validation_error(self) -> None:
        """messages 中含有非字典元素时应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='messages'):
            SessionStateRuntime().build_from_persisted(('not-a-dict',), ())

    def test_build_from_persisted_non_dict_transcript_raises_validation_error(self) -> None:
        """transcript 中含有非字典元素时应抛出 SessionValidationError。"""
        with pytest.raises(SessionValidationError, match='transcript'):
            SessionStateRuntime().build_from_persisted(
                ({'role': 'user', 'content': 'ok'},),
                (42,),
            )