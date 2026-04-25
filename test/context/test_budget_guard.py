"""ISSUE-009 BudgetGuard 直接单元测试。

通过直接实例化 BudgetGuard，不依赖完整的 LocalCodingAgent，
验证每个预算维度和优先级顺序的独立正确性。
"""

from __future__ import annotations

import unittest

from context.budget_guard import BudgetGuard
from context.context_budget import TokenBudgetSnapshot
from core_contracts.config import BudgetConfig
from core_contracts.usage import ModelPricing, TokenUsage


# ---------------------------------------------------------------------------
# 测试辅助函数
# ---------------------------------------------------------------------------

def _guard(budget: BudgetConfig, *, cost_baseline: float = 0.0) -> BudgetGuard:
    """构造最简 BudgetGuard，不设置计费（零成本）。"""
    return BudgetGuard(
        budget=budget,
        pricing=ModelPricing(),
        cost_baseline=cost_baseline,
    )


def _snapshot(*, is_hard_over: bool = False, is_soft_over: bool = False) -> TokenBudgetSnapshot:
    """构造最小 TokenBudgetSnapshot，仅设置关心的标志。"""
    return TokenBudgetSnapshot(
        projected_input_tokens=100,
        output_reserve_tokens=4096,
        hard_input_limit=None,
        soft_input_limit=None,
        is_hard_over=is_hard_over,
        is_soft_over=is_soft_over,
    )


def _call_pre(guard: BudgetGuard, **kwargs) -> str | None:
    """check_pre_model 的便捷调用，未指定参数使用安全默认值。"""
    defaults = dict(
        turns_offset=0,
        turns_this_run=1,
        model_call_count=0,
        snapshot=_snapshot(),
        usage_delta=TokenUsage(),
    )
    defaults.update(kwargs)
    return guard.check_pre_model(**defaults)


# ---------------------------------------------------------------------------
# check_pre_model 测试
# ---------------------------------------------------------------------------

class CheckPreModelTests(unittest.TestCase):
    """验证 check_pre_model 的每个维度及优先级。"""

    def test_no_limits_returns_none(self) -> None:
        """所有 budget 字段为 None 时永远返回 None。"""
        guard = _guard(BudgetConfig())
        self.assertIsNone(_call_pre(guard))

    # ── session_turns ────────────────────────────────────────────────

    def test_session_turns_exceeded_returns_limit(self) -> None:
        """turns_offset + turns_this_run > max_session_turns 时触发。"""
        guard = _guard(BudgetConfig(max_session_turns=3))
        result = _call_pre(guard, turns_offset=3, turns_this_run=1)  # 4 > 3
        self.assertEqual(result, 'session_turns_limit')

    def test_session_turns_at_boundary_not_exceeded(self) -> None:
        """turns_offset + turns_this_run == max_session_turns 时不触发（边界为 >）。"""
        guard = _guard(BudgetConfig(max_session_turns=3))
        result = _call_pre(guard, turns_offset=2, turns_this_run=1)  # 3 == 3, not >
        self.assertIsNone(result)

    # ── model_calls ──────────────────────────────────────────────────

    def test_model_call_limit_reached_returns_limit(self) -> None:
        """model_call_count >= max_model_calls 时触发。"""
        guard = _guard(BudgetConfig(max_model_calls=2))
        result = _call_pre(guard, model_call_count=2)  # 2 >= 2
        self.assertEqual(result, 'model_call_limit')

    def test_model_call_limit_below_not_triggered(self) -> None:
        """model_call_count < max_model_calls 时不触发。"""
        guard = _guard(BudgetConfig(max_model_calls=2))
        result = _call_pre(guard, model_call_count=1)
        self.assertIsNone(result)

    # ── token ────────────────────────────────────────────────────────

    def test_token_hard_over_returns_limit(self) -> None:
        """snapshot.is_hard_over=True 时触发 token_limit。"""
        guard = _guard(BudgetConfig())
        result = _call_pre(guard, snapshot=_snapshot(is_hard_over=True))
        self.assertEqual(result, 'token_limit')

    def test_token_soft_over_only_not_triggered(self) -> None:
        """is_soft_over=True 但 is_hard_over=False 时不触发（soft 由 ISSUE-010/011 处理）。"""
        guard = _guard(BudgetConfig())
        result = _call_pre(guard, snapshot=_snapshot(is_soft_over=True, is_hard_over=False))
        self.assertIsNone(result)

    # ── cost ─────────────────────────────────────────────────────────

    def test_cost_limit_at_zero_triggers(self) -> None:
        """cost_baseline=0.0, max_total_cost_usd=0.0 → 0.0 >= 0.0 → cost_limit。"""
        guard = _guard(BudgetConfig(max_total_cost_usd=0.0), cost_baseline=0.0)
        result = _call_pre(guard)
        self.assertEqual(result, 'cost_limit')

    def test_cost_limit_below_not_triggered(self) -> None:
        """cost_baseline < max_total_cost_usd 且 usage_delta 为零时不触发。"""
        guard = _guard(BudgetConfig(max_total_cost_usd=1.0), cost_baseline=0.5)
        result = _call_pre(guard)
        self.assertIsNone(result)

    # ── 优先级 ───────────────────────────────────────────────────────

    def test_priority_session_turns_beats_model_calls(self) -> None:
        """session_turns 触发时，model_calls 不应被考虑（session_turns 优先）。"""
        guard = _guard(BudgetConfig(max_session_turns=1, max_model_calls=0))
        # turns_offset=1, turns_this_run=1 → 2 > 1 → session_turns_limit
        # model_call_count=0 >= max_model_calls=0 → model_call_limit
        result = _call_pre(guard, turns_offset=1, turns_this_run=1, model_call_count=0)
        self.assertEqual(result, 'session_turns_limit')

    def test_priority_model_calls_beats_token(self) -> None:
        """model_calls 触发时，token 检查不应被执行。"""
        guard = _guard(BudgetConfig(max_model_calls=0))
        result = _call_pre(
            guard,
            model_call_count=0,
            snapshot=_snapshot(is_hard_over=True),
        )
        self.assertEqual(result, 'model_call_limit')

    def test_priority_token_beats_cost(self) -> None:
        """token_limit 优先于 cost_limit。"""
        guard = _guard(BudgetConfig(max_total_cost_usd=0.0), cost_baseline=0.0)
        result = _call_pre(guard, snapshot=_snapshot(is_hard_over=True))
        self.assertEqual(result, 'token_limit')


# ---------------------------------------------------------------------------
# check_post_tool 测试
# ---------------------------------------------------------------------------

class CheckPostToolTests(unittest.TestCase):
    """验证 check_post_tool 的工具调用次数检查。"""

    def test_no_limit_returns_none(self) -> None:
        guard = _guard(BudgetConfig())
        self.assertIsNone(guard.check_post_tool(999))

    def test_limit_reached_returns_stop(self) -> None:
        guard = _guard(BudgetConfig(max_tool_calls=3))
        self.assertEqual(guard.check_post_tool(3), 'tool_call_limit')  # 3 >= 3

    def test_limit_exceeded_returns_stop(self) -> None:
        guard = _guard(BudgetConfig(max_tool_calls=3))
        self.assertEqual(guard.check_post_tool(4), 'tool_call_limit')  # 4 >= 3

    def test_below_limit_returns_none(self) -> None:
        guard = _guard(BudgetConfig(max_tool_calls=3))
        self.assertIsNone(guard.check_post_tool(2))  # 2 < 3


if __name__ == '__main__':
    unittest.main()
