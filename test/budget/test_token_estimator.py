"""ISSUE-009 单元测试：ContextTokenEstimator。"""

from __future__ import annotations

import unittest

from budget.token_estimator import ContextTokenEstimator


ESTIMATOR = ContextTokenEstimator()


class EstimateMessageTokensTests(unittest.TestCase):
    """ContextTokenEstimator.estimate_message 的覆盖测试。"""

    def test_empty_message_returns_overhead_only(self) -> None:
        msg = {'role': 'user', 'content': ''}
        tokens = ESTIMATOR.estimate_message(msg)
        self.assertGreaterEqual(tokens, 5)

    def test_estimate_scales_with_content_length(self) -> None:
        short_msg = {'role': 'user', 'content': 'hi'}
        long_msg = {'role': 'user', 'content': 'hi' * 100}
        self.assertLess(
            ESTIMATOR.estimate_message(short_msg),
            ESTIMATOR.estimate_message(long_msg),
        )

    def test_multimodal_list_content_estimated(self) -> None:
        msg = {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': 'describe this image'},
                {'type': 'image_url', 'image_url': {'url': 'http://example.com/img.png'}},
            ],
        }
        tokens = ESTIMATOR.estimate_message(msg)
        self.assertGreater(tokens, 5)

    def test_non_string_content_falls_back_to_json(self) -> None:
        msg = {'role': 'user', 'content': {'nested': 'object'}}
        tokens = ESTIMATOR.estimate_message(msg)
        self.assertGreater(tokens, 0)


class EstimateMessagesTokensTests(unittest.TestCase):
    """ContextTokenEstimator.estimate_messages 的覆盖测试。"""

    def test_empty_list_returns_base_overhead(self) -> None:
        tokens = ESTIMATOR.estimate_messages([])
        self.assertEqual(tokens, ESTIMATOR.chat_base_tokens)

    def test_multiple_messages_accumulate(self) -> None:
        one = ESTIMATOR.estimate_messages([{'role': 'user', 'content': 'hello'}])
        two = ESTIMATOR.estimate_messages([
            {'role': 'user', 'content': 'hello'},
            {'role': 'assistant', 'content': 'world'},
        ])
        self.assertLess(one, two)


class EstimateToolsTokensTests(unittest.TestCase):
    """ContextTokenEstimator.estimate_tools 的覆盖测试。"""

    def test_empty_tools_returns_zero(self) -> None:
        self.assertEqual(ESTIMATOR.estimate_tools([]), 0)

    def test_nonempty_tools_add_to_projection(self) -> None:
        tools = [{'name': 'read_file', 'description': 'read a file', 'parameters': {}}]
        self.assertGreater(ESTIMATOR.estimate_tools(tools), 0)


if __name__ == '__main__':
    unittest.main()