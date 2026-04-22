"""ISSUE-009 单元测试：TokenBudgetSnapshot 与 check_token_budget。"""

from __future__ import annotations

import unittest

from src.context.token_budget import (
    OUTPUT_RESERVE_TOKENS,
    SOFT_BUFFER_TOKENS,
    TokenBudgetSnapshot,
    check_token_budget,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tools_tokens,
)


class EstimateMessageTokensTests(unittest.TestCase):
    """estimate_message_tokens 的覆盖测试。"""

    def test_empty_message_returns_overhead_only(self) -> None:
        """空 content 消息只返回结构开销（role + _MSG_OVERHEAD）。"""
        msg = {'role': 'user', 'content': ''}
        tokens = estimate_message_tokens(msg)
        # role='user'(4chars→1token) + content=0 + MSG_OVERHEAD=4 → 至少 5
        self.assertGreaterEqual(tokens, 5)

    def test_estimate_scales_with_content_length(self) -> None:
        """更长的内容产生更多 token。"""
        short_msg = {'role': 'user', 'content': 'hi'}
        long_msg = {'role': 'user', 'content': 'hi' * 100}
        self.assertLess(
            estimate_message_tokens(short_msg),
            estimate_message_tokens(long_msg),
        )

    def test_multimodal_list_content_estimated(self) -> None:
        """list 型多模态内容能被估算且大于空内容。"""
        msg = {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': 'describe this image'},
                {'type': 'image_url', 'image_url': {'url': 'http://example.com/img.png'}},
            ],
        }
        tokens = estimate_message_tokens(msg)
        self.assertGreater(tokens, 5)

    def test_non_string_content_falls_back_to_json(self) -> None:
        """非 str/list content 通过 json 序列化兜底估算，结果正整数。"""
        msg = {'role': 'user', 'content': {'nested': 'object'}}
        tokens = estimate_message_tokens(msg)
        self.assertGreater(tokens, 0)


class EstimateMessagesTokensTests(unittest.TestCase):
    """estimate_messages_tokens 的覆盖测试。"""

    def test_empty_list_returns_base_overhead(self) -> None:
        """空列表只有基础 _CHAT_BASE 开销。"""
        from src.context.token_budget import _CHAT_BASE
        tokens = estimate_messages_tokens([])
        self.assertEqual(tokens, _CHAT_BASE)

    def test_multiple_messages_accumulate(self) -> None:
        """多条消息的结果大于单条。"""
        one = estimate_messages_tokens([{'role': 'user', 'content': 'hello'}])
        two = estimate_messages_tokens([
            {'role': 'user', 'content': 'hello'},
            {'role': 'assistant', 'content': 'world'},
        ])
        self.assertLess(one, two)


class EstimateToolsTokensTests(unittest.TestCase):
    """estimate_tools_tokens 的覆盖测试。"""

    def test_empty_tools_returns_zero(self) -> None:
        self.assertEqual(estimate_tools_tokens([]), 0)

    def test_nonempty_tools_add_to_projection(self) -> None:
        tools = [{'name': 'read_file', 'description': 'read a file', 'parameters': {}}]
        self.assertGreater(estimate_tools_tokens(tools), 0)


class CheckTokenBudgetTests(unittest.TestCase):
    """check_token_budget 的核心行为测试。"""

    def _simple_messages(self) -> list[dict]:
        return [{'role': 'user', 'content': 'hello'}]

    def test_no_limit_never_hard_over(self) -> None:
        """无 max_input_tokens 限制时，is_hard_over 和 is_soft_over 永远为 False。"""
        snapshot = check_token_budget(self._simple_messages(), max_input_tokens=None)
        self.assertFalse(snapshot.is_hard_over)
        self.assertFalse(snapshot.is_soft_over)
        self.assertIsNone(snapshot.hard_input_limit)
        self.assertIsNone(snapshot.soft_input_limit)

    def test_within_limit_not_over(self) -> None:
        """投影远小于限制时两个标志均为 False。"""
        snapshot = check_token_budget(
            self._simple_messages(),
            max_input_tokens=100_000,
        )
        self.assertFalse(snapshot.is_hard_over)
        self.assertFalse(snapshot.is_soft_over)

    def test_hard_overflow_when_projected_exceeds_usable(self) -> None:
        """极小限制（1 token）时 is_hard_over=True。"""
        snapshot = check_token_budget(
            self._simple_messages(),
            max_input_tokens=1,
            output_reserve=0,
            soft_buffer=0,
        )
        self.assertTrue(snapshot.is_hard_over)

    def test_soft_over_when_approaching_limit(self) -> None:
        """投影超过 soft_limit 但未超过 hard_limit 时，is_soft_over=True，is_hard_over=False。"""
        messages = self._simple_messages()
        projected = check_token_budget(messages, max_input_tokens=None).projected_input_tokens
        # hard_limit = projected + output_reserve + soft_buffer + 1  → 恰好不超 hard
        # soft_limit = projected + 1 - 1 = projected  → 恰好不超 soft ... 需要细调
        # 设 hard_limit = projected + OUTPUT_RESERVE_TOKENS + 1，soft_buffer 很大
        # 则 usable = hard_limit - OUTPUT_RESERVE = projected + 1 → is_hard_over=False
        # soft_limit = usable - soft_buffer → 如果 soft_buffer > projected+1 → soft_limit ≤ 0
        # 换个策略：output_reserve=0，soft_buffer=0，limit = projected+10 → 不 hard，不 soft
        # 然后 soft_buffer=-1 不行；改用明确数值：
        # output_reserve=0, soft_buffer=1, limit = projected + 1
        # usable = projected+1, is_hard_over: projected > projected+1 → False
        # soft_limit = projected+1-1 = projected, is_soft_over: projected > projected → False
        # 需要 projected > soft_limit，即 projected > usable - soft_buffer
        # 让 soft_buffer = 2, limit = projected + 3, output_reserve = 0
        # usable = projected+3, soft_limit = projected+3-2 = projected+1
        # is_soft_over: projected > projected+1 → False 还是不行
        # 正确推导：需要 soft_limit < projected <= usable
        # soft_limit = usable - soft_buffer < projected <= usable
        # 即 soft_buffer > usable - projected ≥ 0，且 projected ≤ usable
        # 设 output_reserve=0, limit = projected + 5, soft_buffer = 10
        # usable = projected+5, is_hard_over: projected > projected+5 → False ✓
        # soft_limit = projected+5-10 = projected-5 → is_soft_over: projected > projected-5 → True ✓
        limit = projected + 5
        snapshot = check_token_budget(
            messages,
            max_input_tokens=limit,
            output_reserve=0,
            soft_buffer=10,
        )
        self.assertFalse(snapshot.is_hard_over)
        self.assertTrue(snapshot.is_soft_over)

    def test_both_over_when_far_exceeds_limit(self) -> None:
        """极小限制时 is_hard_over 和 is_soft_over 均为 True。"""
        messages = [{'role': 'user', 'content': 'x' * 1000}]
        snapshot = check_token_budget(
            messages,
            max_input_tokens=10,
            output_reserve=0,
            soft_buffer=0,
        )
        self.assertTrue(snapshot.is_hard_over)
        self.assertTrue(snapshot.is_soft_over)

    def test_projected_tokens_included_in_snapshot(self) -> None:
        """projected_input_tokens 应大于 0 且等于消息估算与工具估算之和。"""
        from src.context.token_budget import estimate_messages_tokens, estimate_tools_tokens
        messages = self._simple_messages()
        tools = [{'name': 'read_file', 'description': 'read', 'parameters': {}}]
        snapshot = check_token_budget(messages, tools=tools, max_input_tokens=None)
        expected = estimate_messages_tokens(messages) + estimate_tools_tokens(tools)
        self.assertEqual(snapshot.projected_input_tokens, expected)

    def test_soft_limit_minimum_is_zero(self) -> None:
        """soft_limit 不能为负数，最小为 0。"""
        snapshot = check_token_budget(
            self._simple_messages(),
            max_input_tokens=1,
            output_reserve=100,
            soft_buffer=100,
        )
        self.assertIsNotNone(snapshot.soft_input_limit)
        self.assertGreaterEqual(snapshot.soft_input_limit, 0)


if __name__ == '__main__':
    unittest.main()
