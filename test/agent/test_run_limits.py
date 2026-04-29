"""ISSUE-009/Step-07 RunLimits 直接单元测试。"""

from __future__ import annotations

import unittest

from agent.run_limits import RunLimits
from context.context_gateway import BudgetProjection
from core_contracts.budget import BudgetConfig
from core_contracts.model_pricing import ModelPricing
from core_contracts.token_usage import TokenUsage


def _guard(budget: BudgetConfig, *, cost_baseline: float = 0.0) -> RunLimits:
    return RunLimits(
        budget=budget,
        pricing=ModelPricing(),
        cost_baseline=cost_baseline,
    )


def _snapshot(*, is_hard_over: bool = False, is_soft_over: bool = False) -> BudgetProjection:
    return BudgetProjection(
        projected_input_tokens=100,
        output_reserve_tokens=4096,
        hard_input_limit=None,
        soft_input_limit=None,
        is_hard_over=is_hard_over,
        is_soft_over=is_soft_over,
    )


def _call_pre(guard: RunLimits, **kwargs) -> str | None:
    defaults = dict(
        turns_offset=0,
        turns_this_run=1,
        model_call_count=0,
        snapshot=_snapshot(),
        usage_delta=TokenUsage(),
    )
    defaults.update(kwargs)
    return guard.check_pre_model(**defaults)


class CheckPreModelTests(unittest.TestCase):
    def test_no_limits_returns_none(self) -> None:
        guard = _guard(BudgetConfig())
        self.assertIsNone(_call_pre(guard))

    def test_session_turns_exceeded_returns_limit(self) -> None:
        guard = _guard(BudgetConfig(max_session_turns=3))
        result = _call_pre(guard, turns_offset=3, turns_this_run=1)
        self.assertEqual(result, 'session_turns_limit')

    def test_session_turns_at_boundary_not_exceeded(self) -> None:
        guard = _guard(BudgetConfig(max_session_turns=3))
        result = _call_pre(guard, turns_offset=2, turns_this_run=1)
        self.assertIsNone(result)

    def test_model_call_limit_reached_returns_limit(self) -> None:
        guard = _guard(BudgetConfig(max_model_calls=2))
        result = _call_pre(guard, model_call_count=2)
        self.assertEqual(result, 'model_call_limit')

    def test_model_call_limit_below_not_triggered(self) -> None:
        guard = _guard(BudgetConfig(max_model_calls=2))
        result = _call_pre(guard, model_call_count=1)
        self.assertIsNone(result)

    def test_token_hard_over_returns_limit(self) -> None:
        guard = _guard(BudgetConfig())
        result = _call_pre(guard, snapshot=_snapshot(is_hard_over=True))
        self.assertEqual(result, 'token_limit')

    def test_token_soft_over_only_not_triggered(self) -> None:
        guard = _guard(BudgetConfig())
        result = _call_pre(guard, snapshot=_snapshot(is_soft_over=True, is_hard_over=False))
        self.assertIsNone(result)

    def test_cost_limit_at_zero_triggers(self) -> None:
        guard = _guard(BudgetConfig(max_total_cost_usd=0.0), cost_baseline=0.0)
        result = _call_pre(guard)
        self.assertEqual(result, 'cost_limit')

    def test_cost_limit_below_not_triggered(self) -> None:
        guard = _guard(BudgetConfig(max_total_cost_usd=1.0), cost_baseline=0.5)
        result = _call_pre(guard)
        self.assertIsNone(result)

    def test_priority_session_turns_beats_model_calls(self) -> None:
        guard = _guard(BudgetConfig(max_session_turns=1, max_model_calls=0))
        result = _call_pre(guard, turns_offset=1, turns_this_run=1, model_call_count=0)
        self.assertEqual(result, 'session_turns_limit')

    def test_priority_model_calls_beats_token(self) -> None:
        guard = _guard(BudgetConfig(max_model_calls=0))
        result = _call_pre(
            guard,
            model_call_count=0,
            snapshot=_snapshot(is_hard_over=True),
        )
        self.assertEqual(result, 'model_call_limit')

    def test_priority_token_beats_cost(self) -> None:
        guard = _guard(BudgetConfig(max_total_cost_usd=0.0), cost_baseline=0.0)
        result = _call_pre(guard, snapshot=_snapshot(is_hard_over=True))
        self.assertEqual(result, 'token_limit')


class CheckPostToolTests(unittest.TestCase):
    def test_no_limit_returns_none(self) -> None:
        guard = _guard(BudgetConfig())
        self.assertIsNone(guard.check_post_tool(999))

    def test_limit_reached_returns_stop(self) -> None:
        guard = _guard(BudgetConfig(max_tool_calls=3))
        self.assertEqual(guard.check_post_tool(3), 'tool_call_limit')

    def test_limit_exceeded_returns_stop(self) -> None:
        guard = _guard(BudgetConfig(max_tool_calls=3))
        self.assertEqual(guard.check_post_tool(4), 'tool_call_limit')

    def test_below_limit_returns_none(self) -> None:
        guard = _guard(BudgetConfig(max_tool_calls=3))
        self.assertIsNone(guard.check_post_tool(2))


if __name__ == '__main__':
    unittest.main()