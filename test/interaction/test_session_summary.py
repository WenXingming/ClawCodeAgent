"""SessionInteractionTracker 单元测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core_contracts.interaction_contracts import SessionSummary
from core_contracts.outcomes import AgentRunResult
from core_contracts.primitives import TokenUsage
from interaction import SessionInteractionTracker


def _make_run_result(
    *,
    session_id: str | None = None,
    events: tuple[dict, ...] = (),
) -> AgentRunResult:
    """构造最简 AgentRunResult 供测试使用。"""
    return AgentRunResult(
        final_output='done',
        turns=1,
        tool_calls=0,
        transcript=(),
        events=events,
        usage=TokenUsage(),
        session_id=session_id,
    )


class SessionTrackerStartTests(unittest.TestCase):
    """SessionInteractionTracker.start() 行为测试。"""

    def test_start_without_session_id_initializes_all_counters_to_zero(self) -> None:
        tracker = SessionInteractionTracker.start()
        self.assertIsNone(tracker.session_id)
        self.assertEqual(tracker.tool_calls, 0)
        self.assertEqual(tracker.tool_successes, 0)
        self.assertEqual(tracker.tool_failures, 0)
        self.assertGreater(tracker.started_time, 0.0)

    def test_start_with_session_id_stores_it(self) -> None:
        tracker = SessionInteractionTracker.start(session_id='s-123')
        self.assertEqual(tracker.session_id, 's-123')


class SessionTrackerObserveToolResultTests(unittest.TestCase):
    """SessionInteractionTracker.observe_tool_result() 行为测试。"""

    def setUp(self) -> None:
        self.tracker = SessionInteractionTracker.start()

    def test_successful_call_increments_tool_calls_and_successes(self) -> None:
        self.tracker.observe_tool_result(ok=True)
        self.assertEqual(self.tracker.tool_calls, 1)
        self.assertEqual(self.tracker.tool_successes, 1)
        self.assertEqual(self.tracker.tool_failures, 0)

    def test_failed_call_increments_tool_calls_and_failures(self) -> None:
        self.tracker.observe_tool_result(ok=False)
        self.assertEqual(self.tracker.tool_calls, 1)
        self.assertEqual(self.tracker.tool_successes, 0)
        self.assertEqual(self.tracker.tool_failures, 1)

    def test_multiple_calls_accumulate_correctly(self) -> None:
        for _ in range(3):
            self.tracker.observe_tool_result(ok=True)
        for _ in range(2):
            self.tracker.observe_tool_result(ok=False)
        self.assertEqual(self.tracker.tool_calls, 5)
        self.assertEqual(self.tracker.tool_successes, 3)
        self.assertEqual(self.tracker.tool_failures, 2)


class SessionTrackerUpdateSessionIdTests(unittest.TestCase):
    """SessionInteractionTracker.update_session_id() 行为测试。"""

    def setUp(self) -> None:
        self.tracker = SessionInteractionTracker.start(session_id='initial')

    def test_update_with_valid_id_replaces_stored_id(self) -> None:
        self.tracker.update_session_id('new-id')
        self.assertEqual(self.tracker.session_id, 'new-id')

    def test_update_with_none_keeps_existing_id(self) -> None:
        self.tracker.update_session_id(None)
        self.assertEqual(self.tracker.session_id, 'initial')

    def test_update_with_empty_string_ignored_as_falsy(self) -> None:
        """update_session_id 使用 truthiness 判断，空字符串视为无效。"""
        self.tracker.update_session_id('')
        self.assertEqual(self.tracker.session_id, 'initial')

    def test_update_from_none_to_valid(self) -> None:
        tracker = SessionInteractionTracker.start()
        self.assertIsNone(tracker.session_id)
        tracker.update_session_id('late-id')
        self.assertEqual(tracker.session_id, 'late-id')


class SessionTrackerObserveRunResultTests(unittest.TestCase):
    """SessionInteractionTracker.observe_run_result() 行为测试。"""

    def setUp(self) -> None:
        self.tracker = SessionInteractionTracker.start()

    def test_observes_session_id_from_result(self) -> None:
        result = _make_run_result(session_id='res-session')
        self.tracker.observe_run_result(result, current_session_id='current')
        self.assertEqual(self.tracker.session_id, 'res-session')

    def test_falls_back_to_current_session_id_when_result_has_none(self) -> None:
        result = _make_run_result(session_id=None)
        self.tracker.observe_run_result(result, current_session_id='fallback')
        self.assertEqual(self.tracker.session_id, 'fallback')

    def test_tool_result_without_ok_field_treated_as_failure(self) -> None:
        """tool_result 事件缺失 ok 字段时，bool(None) 为 False，计为失败调用。"""
        result = _make_run_result(
            session_id='s',
            events=(
                {'type': 'tool_result', 'ok': True},
                {'type': 'tool_result'},
                {'type': 'tool_result', 'ok': False},
                {'type': 'other_event'},
            ),
        )
        self.tracker.observe_run_result(result, current_session_id='c')
        # 三个 tool_result 事件全部计数：ok=True 计入成功，其余两个计入失败
        self.assertEqual(self.tracker.tool_calls, 3)
        self.assertEqual(self.tracker.tool_successes, 1)
        self.assertEqual(self.tracker.tool_failures, 2)

    def test_only_consumes_tool_result_event_type(self) -> None:
        result = _make_run_result(
            session_id='s',
            events=(
                {'type': 'model_start', 'ok': True},
                {'type': 'tool_result', 'ok': True},
                {'type': 'model_turn', 'ok': False},
            ),
        )
        self.tracker.observe_run_result(result, current_session_id='c')
        self.assertEqual(self.tracker.tool_calls, 1)
        self.assertEqual(self.tracker.tool_successes, 1)
        self.assertEqual(self.tracker.tool_failures, 0)


class SessionTrackerToSummaryTests(unittest.TestCase):
    """SessionInteractionTracker.to_summary() 行为测试。"""

    def test_returns_session_summary_instance(self) -> None:
        tracker = SessionInteractionTracker.start(session_id='s-1')
        summary = tracker.to_summary()
        self.assertIsInstance(summary, SessionSummary)

    def test_projects_stored_accumulators(self) -> None:
        tracker = SessionInteractionTracker.start(session_id='s-1')
        tracker.observe_tool_result(ok=True)
        tracker.observe_tool_result(ok=True)
        tracker.observe_tool_result(ok=False)

        summary = tracker.to_summary()
        self.assertEqual(summary.session_id, 's-1')
        self.assertEqual(summary.tool_calls, 3)
        self.assertEqual(summary.tool_successes, 2)
        self.assertEqual(summary.tool_failures, 1)
        self.assertGreater(summary.wall_time_seconds, 0.0)

    def test_success_rate_is_zero_when_no_tool_calls(self) -> None:
        tracker = SessionInteractionTracker.start()
        summary = tracker.to_summary()
        self.assertEqual(summary.success_rate, 0.0)

    def test_success_rate_is_correct_ratio(self) -> None:
        tracker = SessionInteractionTracker.start()
        tracker.observe_tool_result(ok=True)
        tracker.observe_tool_result(ok=False)
        summary = tracker.to_summary()
        self.assertAlmostEqual(summary.success_rate, 0.5)


if __name__ == '__main__':
    unittest.main()
