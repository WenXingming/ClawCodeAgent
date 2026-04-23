"""ISSUE-007 会话契约测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from core_contracts.config import AgentRuntimeConfig, ModelConfig
from core_contracts.usage import TokenUsage
from session.session_contracts import StoredAgentSession


_TEST_TMP_ROOT = (Path(__file__).resolve().parent / '.tmp').resolve()


def _make_test_dir() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = _TEST_TMP_ROOT / f'case-{uuid4().hex}'
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


class StoredAgentSessionTests(unittest.TestCase):
    """验证会话快照契约的解析与兼容行为。"""

    def test_stored_agent_session_round_trip(self) -> None:
        workspace = _make_test_dir()
        session = StoredAgentSession(
            session_id='session-001',
            model_config=ModelConfig(model='demo-model'),
            runtime_config=AgentRuntimeConfig(cwd=workspace),
            messages=({'role': 'user', 'content': '你好'},),
            transcript=({'role': 'assistant', 'content': '已完成'},),
            events=({'type': 'model_turn', 'turn': 1},),
            final_output='已完成',
            turns=1,
            tool_calls=0,
            usage=TokenUsage(input_tokens=12, output_tokens=8),
            total_cost_usd=0.123,
            stop_reason='stop',
            file_history=({'action': 'write_file', 'path': 'demo.txt'},),
            scratchpad_directory=str(workspace / 'scratchpad'),
        )

        restored = StoredAgentSession.from_dict(session.to_dict())

        self.assertEqual(restored.session_id, session.session_id)
        self.assertEqual(restored.model_config, session.model_config)
        self.assertEqual(restored.runtime_config.cwd, session.runtime_config.cwd.resolve())
        self.assertEqual(restored.messages, session.messages)
        self.assertEqual(restored.transcript, session.transcript)
        self.assertEqual(restored.events, session.events)
        self.assertEqual(restored.usage, session.usage)
        self.assertEqual(restored.total_cost_usd, session.total_cost_usd)
        self.assertEqual(restored.schema_version, 1)

    def test_stored_agent_session_defaults_optional_fields(self) -> None:
        workspace = _make_test_dir()
        restored = StoredAgentSession.from_dict(
            {
                'session_id': 'minimal',
                'model_config': {'model': 'demo-model'},
                'runtime_config': {'cwd': str(workspace)},
                'messages': [{'role': 'user', 'content': 'hi'}],
            }
        )

        self.assertEqual(restored.schema_version, 1)
        self.assertEqual(restored.transcript, ())
        self.assertEqual(restored.events, ())
        self.assertEqual(restored.final_output, '')
        self.assertEqual(restored.usage, TokenUsage())
        self.assertEqual(restored.file_history, ())
        self.assertIsNone(restored.stop_reason)

    def test_stored_agent_session_supports_camel_case_fields(self) -> None:
        workspace = _make_test_dir()
        restored = StoredAgentSession.from_dict(
            {
                'schemaVersion': 3,
                'sessionId': 'camel-case',
                'modelConfig': {'model': 'demo-model'},
                'runtimeConfig': {'cwd': str(workspace)},
                'messages': [{'role': 'user', 'content': 'hi'}],
                'finalOutput': 'done',
                'toolCalls': 2,
                'totalCostUsd': 1.25,
                'stopReason': 'completed',
                'fileHistory': [{'action': 'edit_file'}],
                'scratchpadDirectory': str(workspace / 'scratchpad'),
            }
        )

        self.assertEqual(restored.schema_version, 3)
        self.assertEqual(restored.session_id, 'camel-case')
        self.assertEqual(restored.final_output, 'done')
        self.assertEqual(restored.tool_calls, 2)
        self.assertEqual(restored.total_cost_usd, 1.25)
        self.assertEqual(restored.stop_reason, 'completed')
        self.assertEqual(restored.file_history, ({'action': 'edit_file'},))

    def test_stored_agent_session_requires_core_fields(self) -> None:
        with self.assertRaises(ValueError):
            StoredAgentSession.from_dict({'session_id': 'missing-messages'})


if __name__ == '__main__':
    unittest.main()
