"""ISSUE-007 会话持久化与基础恢复测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from uuid import uuid4

from src.core_contracts import AgentRuntimeConfig, ModelConfig, TokenUsage
from src.session import StoredAgentSession, load_agent_session, save_agent_session


_TEST_TMP_ROOT = (Path(__file__).resolve().parent / '.tmp').resolve()


def _make_test_dir() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = _TEST_TMP_ROOT / f'case-{uuid4().hex}'
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


class SessionStoreTests(unittest.TestCase):
    """验证 save/load 的基础行为与容错。"""

    def _make_session(self, workspace: Path, *, session_id: str = 'session-001') -> StoredAgentSession:
        return StoredAgentSession(
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

    def test_save_and_load_round_trip(self) -> None:
        workspace = _make_test_dir()
        session = self._make_session(workspace)
        path = save_agent_session(session, directory=workspace / 'sessions')
        restored = load_agent_session('session-001', directory=workspace / 'sessions')
        self.assertTrue(path.exists())
        self.assertEqual(restored.session_id, session.session_id)
        self.assertEqual(restored.model_config, session.model_config)
        self.assertEqual(restored.runtime_config.cwd, session.runtime_config.cwd.resolve())
        self.assertEqual(restored.messages, session.messages)
        self.assertEqual(restored.transcript, session.transcript)
        self.assertEqual(restored.events, session.events)
        self.assertEqual(restored.usage, session.usage)
        self.assertEqual(restored.total_cost_usd, session.total_cost_usd)
        self.assertEqual(restored.scratchpad_directory, session.scratchpad_directory)

    def test_save_creates_session_directory(self) -> None:
        workspace = _make_test_dir()
        target_dir = workspace / 'nested' / 'sessions'
        save_agent_session(self._make_session(workspace), directory=target_dir)
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
        restored = load_agent_session('minimal', directory=path)

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
            load_agent_session('broken', directory=directory)

    def test_load_restores_config_objects(self) -> None:
        workspace = _make_test_dir()
        session = self._make_session(workspace, session_id='restore-001')
        save_agent_session(session, directory=workspace / 'sessions')
        restored = load_agent_session('restore-001', directory=workspace / 'sessions')

        self.assertIsInstance(restored.model_config, ModelConfig)
        self.assertIsInstance(restored.runtime_config, AgentRuntimeConfig)
        self.assertEqual(restored.runtime_config.cwd, workspace.resolve())

    def test_save_and_load_preserve_utf8_content(self) -> None:
        workspace = _make_test_dir()
        session = self._make_session(workspace, session_id='utf8-001')
        session = StoredAgentSession(
            session_id=session.session_id,
            model_config=session.model_config,
            runtime_config=session.runtime_config,
            messages=({'role': 'user', 'content': '中文内容：你好，世界'},),
            final_output='已保存中文',
        )
        save_agent_session(session, directory=workspace / 'sessions')
        restored = load_agent_session('utf8-001', directory=workspace / 'sessions')

        self.assertEqual(restored.messages[0]['content'], '中文内容：你好，世界')
        self.assertEqual(restored.final_output, '已保存中文')


if __name__ == '__main__':
    unittest.main()
