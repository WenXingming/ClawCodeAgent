"""ISSUE-007 会话快照契约测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from core_contracts.config import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.config import ToolPermissionPolicy
from core_contracts.config import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from core_contracts.primitives import TokenUsage
from core_contracts.session import AgentSessionSnapshot


_TEST_TMP_ROOT = (Path(__file__).resolve().parent / '.tmp').resolve()


def _make_test_dir() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = _TEST_TMP_ROOT / f'case-{uuid4().hex}'
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


class AgentSessionSnapshotTests(unittest.TestCase):
    """验证会话快照契约的解析与兼容行为。"""

    def test_agent_session_snapshot_round_trip(self) -> None:
        workspace = _make_test_dir()
        session_snapshot = AgentSessionSnapshot(
            session_id='session-001',
            model_config=ModelConfig(model='demo-model'),
            workspace_scope=WorkspaceScope(cwd=workspace),
            execution_policy=ExecutionPolicy(),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            budget_config=BudgetConfig(),
            session_paths=SessionPaths(),
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
            mcp_capability_shortlist=(
                {
                    'handle': 'mcp:tavily:tavily_search',
                    'tool_name': 'tavily_search',
                    'server_name': 'tavily',
                },
            ),
            materialized_mcp_capability_handles=('mcp:tavily:tavily_search',),
        )

        restored = AgentSessionSnapshot.from_dict(session_snapshot.to_dict())

        self.assertEqual(restored.session_id, session_snapshot.session_id)
        self.assertEqual(restored.model_config, session_snapshot.model_config)
        self.assertEqual(restored.workspace_scope.cwd, session_snapshot.workspace_scope.cwd.resolve())
        self.assertEqual(restored.messages, session_snapshot.messages)
        self.assertEqual(restored.transcript, session_snapshot.transcript)
        self.assertEqual(restored.events, session_snapshot.events)
        self.assertEqual(restored.usage, session_snapshot.usage)
        self.assertEqual(restored.total_cost_usd, session_snapshot.total_cost_usd)
        self.assertEqual(restored.mcp_capability_shortlist, session_snapshot.mcp_capability_shortlist)
        self.assertEqual(
            restored.materialized_mcp_capability_handles,
            session_snapshot.materialized_mcp_capability_handles,
        )
        self.assertEqual(restored.schema_version, 1)

    def test_agent_session_snapshot_defaults_optional_fields(self) -> None:
        workspace = _make_test_dir()
        restored = AgentSessionSnapshot.from_dict(
            {
                'session_id': 'minimal',
                'model_config': {'model': 'demo-model'},
                'workspace_scope': {'cwd': str(workspace)},
                'messages': [{'role': 'user', 'content': 'hi'}],
            }
        )

        self.assertEqual(restored.schema_version, 1)
        self.assertEqual(restored.workspace_scope.cwd, workspace.resolve())
        self.assertEqual(restored.transcript, ())
        self.assertEqual(restored.events, ())
        self.assertEqual(restored.final_output, '')
        self.assertEqual(restored.usage, TokenUsage())
        self.assertEqual(restored.file_history, ())
        self.assertEqual(restored.mcp_capability_shortlist, ())
        self.assertEqual(restored.materialized_mcp_capability_handles, ())
        self.assertIsNone(restored.stop_reason)

    def test_agent_session_snapshot_supports_camel_case_fields(self) -> None:
        workspace = _make_test_dir()
        restored = AgentSessionSnapshot.from_dict(
            {
                'schemaVersion': 3,
                'sessionId': 'camel-case',
                'modelConfig': {'model': 'demo-model'},
                'workspaceScope': {'cwd': str(workspace)},
                'executionPolicy': {'maxTurns': 3},
                'contextPolicy': {'compactPreserveMessages': 2},
                'budgetConfig': {'maxToolCalls': 5},
                'sessionPaths': {'sessionDirectory': str(workspace / 'sessions')},
                'messages': [{'role': 'user', 'content': 'hi'}],
                'finalOutput': 'done',
                'toolCalls': 2,
                'totalCostUsd': 1.25,
                'stopReason': 'completed',
                'fileHistory': [{'action': 'edit_file'}],
                'scratchpadDirectory': str(workspace / 'scratchpad'),
                'mcpCapabilityShortlist': [{'handle': 'mcp:tavily:tavily_search'}],
                'materializedMcpCapabilityHandles': ['mcp:tavily:tavily_search'],
            }
        )

        self.assertEqual(restored.schema_version, 3)
        self.assertEqual(restored.session_id, 'camel-case')
        self.assertEqual(restored.execution_policy.max_turns, 3)
        self.assertEqual(restored.context_policy.compact_preserve_messages, 2)
        self.assertEqual(restored.budget_config.max_tool_calls, 5)
        self.assertEqual(restored.final_output, 'done')
        self.assertEqual(restored.tool_calls, 2)
        self.assertEqual(restored.total_cost_usd, 1.25)
        self.assertEqual(restored.stop_reason, 'completed')
        self.assertEqual(restored.file_history, ({'action': 'edit_file'},))
        self.assertEqual(restored.mcp_capability_shortlist, ({'handle': 'mcp:tavily:tavily_search'},))
        self.assertEqual(restored.materialized_mcp_capability_handles, ('mcp:tavily:tavily_search',))

    def test_agent_session_snapshot_requires_core_fields(self) -> None:
        with self.assertRaises(ValueError):
            AgentSessionSnapshot.from_dict({'session_id': 'missing-messages'})


if __name__ == '__main__':
    unittest.main()