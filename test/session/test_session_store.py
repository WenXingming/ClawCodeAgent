"""ISSUE-007 会话持久化与基础恢复测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from uuid import uuid4

from core_contracts.config import AgentRuntimeConfig, ModelConfig
from core_contracts.token_usage import TokenUsage
from session.session_snapshot import AgentSessionSnapshot
from session.session_store import AgentSessionStore


_TEST_TMP_ROOT = (Path(__file__).resolve().parent / '.tmp').resolve()


def _make_test_dir() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = _TEST_TMP_ROOT / f'case-{uuid4().hex}'
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


class SessionStoreTests(unittest.TestCase):
    """验证 AgentSessionStore 的基础行为与容错。"""

    def _make_session_snapshot(self, workspace: Path, *, session_id: str = 'session-001') -> AgentSessionSnapshot:
        return AgentSessionSnapshot(
            session_id=session_id,
            model_config=ModelConfig(model='demo-model'),
            runtime_config=AgentRuntimeConfig(cwd=workspace),
            messages=(
                {'role': 'user', 'content': '你好，世界'},
                {'role': 'assistant', 'content': '收到'},
            ),
            transcript=({'role': 'assistant', 'content': '收到'},),
            events=({'type': 'model_turn', 'turn': 1},),
            final_output='收到',
            turns=1,
            tool_calls=0,
            usage=TokenUsage(input_tokens=10, output_tokens=4),
            total_cost_usd=0.25,
            stop_reason='stop',
            scratchpad_directory=str(workspace / 'scratchpad'),
        )

    def _make_store(self, workspace: Path, *, directory: Path | None = None) -> AgentSessionStore:
        return AgentSessionStore(directory or workspace / 'sessions')

    def test_save_and_load_round_trip(self) -> None:
        workspace = _make_test_dir()
        session_snapshot = self._make_session_snapshot(workspace)
        store = self._make_store(workspace)
        path = store.save(session_snapshot)
        restored = store.load('session-001')
        self.assertTrue(path.exists())
        self.assertEqual(restored.session_id, session_snapshot.session_id)
        self.assertEqual(restored.model_config, session_snapshot.model_config)
        self.assertEqual(restored.runtime_config.cwd, session_snapshot.runtime_config.cwd.resolve())
        self.assertEqual(restored.messages, session_snapshot.messages)
        self.assertEqual(restored.transcript, session_snapshot.transcript)
        self.assertEqual(restored.events, session_snapshot.events)
        self.assertEqual(restored.usage, session_snapshot.usage)
        self.assertEqual(restored.total_cost_usd, session_snapshot.total_cost_usd)
        self.assertEqual(restored.scratchpad_directory, session_snapshot.scratchpad_directory)

    def test_store_instance_round_trip(self) -> None:
        workspace = _make_test_dir()
        session_snapshot = self._make_session_snapshot(workspace, session_id='instance-001')
        store = self._make_store(workspace)

        path = store.save(session_snapshot)
        restored = store.load('instance-001')

        self.assertTrue(path.exists())
        self.assertEqual(store.directory, (workspace / 'sessions').resolve())
        self.assertEqual(restored.session_id, session_snapshot.session_id)
        self.assertEqual(restored.messages, session_snapshot.messages)

    def test_save_creates_session_directory(self) -> None:
        workspace = _make_test_dir()
        target_dir = workspace / 'nested' / 'sessions'
        store = self._make_store(workspace, directory=target_dir)
        store.save(self._make_session_snapshot(workspace))
        self.assertTrue(target_dir.exists())
        self.assertTrue((target_dir / 'session-001.json').exists())

    def test_load_defaults_missing_optional_fields(self) -> None:
        workspace = _make_test_dir()
        path = (workspace / 'sessions')
        path.mkdir(parents=True, exist_ok=True)
        (path / 'minimal.json').write_text(
            json.dumps(
                {
                    'session_id': 'minimal',
                    'model_config': {'model': 'demo-model'},
                    'runtime_config': {'cwd': str(workspace)},
                    'messages': [{'role': 'user', 'content': 'hi'}],
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        restored = self._make_store(workspace, directory=path).load('minimal')

        self.assertEqual(restored.transcript, ())
        self.assertEqual(restored.events, ())
        self.assertEqual(restored.final_output, '')
        self.assertEqual(restored.usage, TokenUsage())
        self.assertEqual(restored.file_history, ())
        self.assertIsNone(restored.stop_reason)

    def test_load_raises_value_error_for_corrupted_json(self) -> None:
        workspace = _make_test_dir()
        directory = workspace / 'sessions'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'broken.json').write_text('{not-json', encoding='utf-8')

        with self.assertRaises(ValueError):
            self._make_store(workspace, directory=directory).load('broken')

    def test_load_restores_config_objects(self) -> None:
        workspace = _make_test_dir()
        session_snapshot = self._make_session_snapshot(workspace, session_id='restore-001')
        store = self._make_store(workspace)
        store.save(session_snapshot)
        restored = store.load('restore-001')

        self.assertIsInstance(restored.model_config, ModelConfig)
        self.assertIsInstance(restored.runtime_config, AgentRuntimeConfig)
        self.assertEqual(restored.runtime_config.cwd, workspace.resolve())

    def test_save_and_load_preserve_utf8_content(self) -> None:
        workspace = _make_test_dir()
        session_snapshot = self._make_session_snapshot(workspace, session_id='utf8-001')
        session_snapshot = AgentSessionSnapshot(
            session_id=session_snapshot.session_id,
            model_config=session_snapshot.model_config,
            runtime_config=session_snapshot.runtime_config,
            messages=({'role': 'user', 'content': '中文内容：你好，世界'},),
            final_output='已保存中文',
        )
        store = self._make_store(workspace)
        store.save(session_snapshot)
        restored = store.load('utf8-001')

        self.assertEqual(restored.messages[0]['content'], '中文内容：你好，世界')
        self.assertEqual(restored.final_output, '已保存中文')


if __name__ == '__main__':
    unittest.main()
