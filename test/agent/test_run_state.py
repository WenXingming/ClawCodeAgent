"""AgentRunState 单元测试。"""

from __future__ import annotations

import unittest

from agent.run_state import AgentRunState
from core_contracts.protocol import ToolCall, ToolExecutionResult
from core_contracts.token_usage import TokenUsage
from session.session_state import AgentSessionState


class AgentRunStateTests(unittest.TestCase):
    """验证动态运行态对象的聚合行为。"""

    def test_for_resumed_session_restores_history_baselines(self) -> None:
        session_state = AgentSessionState.from_persisted(
            messages=[{'role': 'user', 'content': '旧提问'}],
            transcript=[{'role': 'user', 'content': '旧提问'}],
        )

        run_state = AgentRunState.for_resumed_session(
            session_state=session_state,
            session_id='resume-001',
            turns_offset=3,
            usage_baseline=TokenUsage(input_tokens=8, output_tokens=5),
            cost_baseline=0.25,
            tool_call_count=2,
            mcp_capability_shortlist=[{'handle': 'mcp:tavily:tavily_search'}],
            materialized_mcp_capability_handles=['mcp:tavily:tavily_search'],
        )

        self.assertEqual(run_state.session_id, 'resume-001')
        self.assertEqual(run_state.turns_offset, 3)
        self.assertEqual(run_state.usage_baseline.input_tokens, 8)
        self.assertEqual(run_state.tool_call_count, 2)
        self.assertEqual(
            run_state.mcp_capability_candidates(),
            ({'handle': 'mcp:tavily:tavily_search'},),
        )
        self.assertEqual(
            run_state.materialized_mcp_capabilities(),
            ('mcp:tavily:tavily_search',),
        )

    def test_begin_turn_and_usage_total_reflect_runtime_progress(self) -> None:
        run_state = AgentRunState.for_new_session(
            session_state=AgentSessionState.create('初始化'),
            session_id='session-001',
        )
        run_state.turns_offset = 2
        run_state.usage_baseline = TokenUsage(input_tokens=10, output_tokens=4)
        run_state.usage_delta = TokenUsage(input_tokens=3, output_tokens=2)

        run_state.begin_turn(2)

        self.assertEqual(run_state.turn_index, 2)
        self.assertEqual(run_state.turns_this_run, 2)
        self.assertEqual(run_state.turns_total, 4)
        self.assertEqual(run_state.usage_total.input_tokens, 13)
        self.assertEqual(run_state.usage_total.output_tokens, 6)

    def test_record_tool_result_updates_counter_without_polluting_session_state(self) -> None:
        run_state = AgentRunState.for_new_session(
            session_state=AgentSessionState.create('初始化'),
            session_id='session-001',
        )

        run_state.record_tool_result(
            ToolCall(id='tool-001', name='list_dir', arguments={'path': '.'}),
            ToolExecutionResult(name='list_dir', ok=True, content='[]'),
        )

        self.assertEqual(run_state.tool_call_count, 1)
        transcript = run_state.session_state.transcript()
        self.assertEqual(transcript[-1]['role'], 'tool')

    def test_update_mcp_capability_window_replaces_previous_window(self) -> None:
        run_state = AgentRunState.for_new_session(
            session_state=AgentSessionState.create('初始化'),
            session_id='session-001',
        )
        run_state.update_mcp_capability_window(
            shortlist=[{'handle': 'old'}],
            materialized_handles=['old'],
        )

        run_state.update_mcp_capability_window(
            shortlist=[{'handle': 'new', 'tool_name': 'search'}],
            materialized_handles=['new'],
        )

        self.assertEqual(
            run_state.mcp_capability_candidates(),
            ({'handle': 'new', 'tool_name': 'search'},),
        )
        self.assertEqual(run_state.materialized_mcp_capabilities(), ('new',))


if __name__ == '__main__':
    unittest.main()