"""ISSUE-009 单元测试：ContextTokenBudgetEvaluator。"""

from __future__ import annotations

import unittest

from context.context_gateway import (
    BudgetProjector,
    OUTPUT_RESERVE_TOKENS,
    SOFT_BUFFER_TOKENS,
)
from context.context_gateway import BudgetProjection
from context.context_gateway import TokenEstimator


ESTIMATOR = TokenEstimator()
PROJECTOR = BudgetProjector(token_estimator=ESTIMATOR)


class BudgetProjectionTests(unittest.TestCase):
    """BudgetProjector.project 的核心行为测试。"""

    def _simple_messages(self) -> list[dict]:
        return [{'role': 'user', 'content': 'hello'}]

    def test_no_limit_never_hard_over(self) -> None:
        snapshot = PROJECTOR.project(self._simple_messages(), max_input_tokens=None)
        self.assertFalse(snapshot.is_hard_over)
        self.assertFalse(snapshot.is_soft_over)
        self.assertIsNone(snapshot.hard_input_limit)
        self.assertIsNone(snapshot.soft_input_limit)

    def test_within_limit_not_over(self) -> None:
        snapshot = PROJECTOR.project(
            self._simple_messages(),
            max_input_tokens=100_000,
        )
        self.assertFalse(snapshot.is_hard_over)
        self.assertFalse(snapshot.is_soft_over)

    def test_hard_overflow_when_projected_exceeds_usable(self) -> None:
        snapshot = PROJECTOR.project(
            self._simple_messages(),
            max_input_tokens=1,
            output_reserve_tokens=0,
            soft_buffer_tokens=0,
        )
        self.assertTrue(snapshot.is_hard_over)

    def test_soft_over_when_approaching_limit(self) -> None:
        messages = self._simple_messages()
        projected = PROJECTOR.project(messages, max_input_tokens=None).projected_input_tokens
        limit = projected + 5
        snapshot = PROJECTOR.project(
            messages,
            max_input_tokens=limit,
            output_reserve_tokens=0,
            soft_buffer_tokens=10,
        )
        self.assertFalse(snapshot.is_hard_over)
        self.assertTrue(snapshot.is_soft_over)

    def test_both_over_when_far_exceeds_limit(self) -> None:
        messages = [{'role': 'user', 'content': 'x' * 1000}]
        snapshot = PROJECTOR.project(
            messages,
            max_input_tokens=10,
            output_reserve_tokens=0,
            soft_buffer_tokens=0,
        )
        self.assertTrue(snapshot.is_hard_over)
        self.assertTrue(snapshot.is_soft_over)

    def test_projected_tokens_included_in_snapshot(self) -> None:
        messages = self._simple_messages()
        tools = [{'name': 'read_file', 'description': 'read', 'parameters': {}}]
        snapshot = PROJECTOR.project(messages, tools=tools, max_input_tokens=None)
        expected = ESTIMATOR.estimate_messages(messages) + ESTIMATOR.estimate_tools(tools)
        self.assertEqual(snapshot.projected_input_tokens, expected)

    def test_soft_limit_minimum_is_zero(self) -> None:
        snapshot = PROJECTOR.project(
            self._simple_messages(),
            max_input_tokens=1,
            output_reserve_tokens=100,
            soft_buffer_tokens=100,
        )
        self.assertIsNotNone(snapshot.soft_input_limit)
        self.assertGreaterEqual(snapshot.soft_input_limit, 0)

    def test_defaults_exposed_from_module(self) -> None:
        snapshot = BudgetProjection(
            projected_input_tokens=1,
            output_reserve_tokens=OUTPUT_RESERVE_TOKENS,
            hard_input_limit=10,
            soft_input_limit=0,
            is_hard_over=False,
            is_soft_over=False,
        )
        self.assertEqual(snapshot.output_reserve_tokens, OUTPUT_RESERVE_TOKENS)
        self.assertEqual(PROJECTOR.soft_buffer_tokens, SOFT_BUFFER_TOKENS)


if __name__ == '__main__':
    unittest.main()