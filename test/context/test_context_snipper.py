"""ISSUE-010 ContextSnipper 单元测试。

测试内容：
- ContextSnipper.snip：剪裁范围、边界、统计
"""

from __future__ import annotations

import unittest

from context.context_gateway import SnipResult, Snipper, TokenEstimator


SNIPPER = Snipper(token_estimator=TokenEstimator())


# ---------------------------------------------------------------------------
# 辅助构造函数
# ---------------------------------------------------------------------------

def _tool_msg(content: str = 'output', *, tool_call_id: str = 'call_1', name: str = 'read_file') -> dict:
    return {'role': 'tool', 'content': content, 'tool_call_id': tool_call_id, 'name': name}


def _assistant_msg(content: str = '', *, tool_calls: list | None = None) -> dict:
    msg: dict = {'role': 'assistant', 'content': content}
    if tool_calls is not None:
        msg['tool_calls'] = tool_calls
    return msg


def _system_msg(content: str = 'system prompt') -> dict:
    return {'role': 'system', 'content': content}


def _user_msg(content: str = 'hi') -> dict:
    return {'role': 'user', 'content': content}


def _tombstone() -> dict:
    return {
        'role': 'tool',
        'content': '<system-reminder>\nOlder tool result (read_file) was snipped to save context.\nPreview: (empty)\n</system-reminder>',
        'tool_call_id': 'call_1',
        'name': 'read_file',
    }


# ---------------------------------------------------------------------------
# snip 候选规则测试
# ---------------------------------------------------------------------------

class IsSnippableTests(unittest.TestCase):

    def test_tool_message_is_snippable(self) -> None:
        self.assertTrue(SNIPPER._is_snippable(_tool_msg('some output')))

    def test_tombstone_is_not_snippable(self) -> None:
        self.assertFalse(SNIPPER._is_snippable(_tombstone()))

    def test_system_message_is_not_snippable(self) -> None:
        self.assertFalse(SNIPPER._is_snippable(_system_msg()))

    def test_user_message_is_not_snippable(self) -> None:
        self.assertFalse(SNIPPER._is_snippable(_user_msg()))

    def test_short_assistant_without_tool_calls_is_not_snippable(self) -> None:
        self.assertFalse(SNIPPER._is_snippable(_assistant_msg('short reply')))

    def test_long_assistant_without_tool_calls_is_snippable(self) -> None:
        long_content = 'x' * 301
        self.assertTrue(SNIPPER._is_snippable(_assistant_msg(long_content)))

    def test_assistant_with_tool_calls_is_snippable(self) -> None:
        msg = _assistant_msg('', tool_calls=[{'id': 'call_1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}])
        self.assertTrue(SNIPPER._is_snippable(msg))

    def test_assistant_exactly_at_threshold_is_not_snippable(self) -> None:
        """300 字符刚好等于阈值，不可剪（> 才触发）。"""
        content = 'x' * 300
        self.assertFalse(SNIPPER._is_snippable(_assistant_msg(content)))


# ---------------------------------------------------------------------------
# tombstone 生成测试
# ---------------------------------------------------------------------------

class MakeTombstoneTests(unittest.TestCase):

    def test_tool_message_preserves_protocol_fields(self) -> None:
        original = _tool_msg('file content here', tool_call_id='call_42', name='write_file')
        tomb = SNIPPER._make_tombstone(original)
        self.assertEqual(tomb['role'], 'tool')
        self.assertEqual(tomb['tool_call_id'], 'call_42')
        self.assertEqual(tomb['name'], 'write_file')

    def test_tool_message_content_is_system_reminder(self) -> None:
        tomb = SNIPPER._make_tombstone(_tool_msg('abc'))
        self.assertTrue(tomb['content'].startswith('<system-reminder>\nOlder tool result'))

    def test_assistant_with_tool_calls_preserves_tool_calls(self) -> None:
        tc = [{'id': 'c1', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{}'}}]
        original = _assistant_msg('I will run this', tool_calls=tc)
        tomb = SNIPPER._make_tombstone(original)
        self.assertEqual(tomb['tool_calls'], tc)
        self.assertIn('assistant message with tool calls', tomb['content'])

    def test_tombstone_does_not_include_full_original_content(self) -> None:
        """tombstone 的 Preview 中仅包含截断后的预览，超长内容不应完整出现。"""
        long_content = 'ORIGINAL_' * 30   # 270 chars > 120 limit
        original = _tool_msg(long_content)
        tomb = SNIPPER._make_tombstone(original)
        # 完整原始内容不应屑现（被截断为至多 120 chars）
        self.assertNotIn(long_content, tomb['content'])
        # 但应保留 tombstone 结构
        self.assertIn('<system-reminder>', tomb['content'])

    def test_long_content_preview_is_truncated(self) -> None:
        original = _tool_msg('x' * 200)
        tomb = SNIPPER._make_tombstone(original)
        # 预览最多 120 字符
        preview_line = [l for l in tomb['content'].splitlines() if l.startswith('Preview:')][0]
        preview = preview_line[len('Preview: '):]
        self.assertLessEqual(len(preview), 123)  # 120 + '...' = 123


# ---------------------------------------------------------------------------
# ContextSnipper.snip 测试
# ---------------------------------------------------------------------------

class SnipSessionTests(unittest.TestCase):

    def test_empty_messages_returns_zero(self) -> None:
        result = SNIPPER.snip([], preserve_messages=4)
        self.assertEqual(result, SnipResult(snipped_count=0, tokens_removed=0))

    def test_no_snippable_candidates_returns_zero(self) -> None:
        msgs = [_system_msg(), _user_msg('task'), _assistant_msg('done')]
        result = SNIPPER.snip(msgs, preserve_messages=4)
        self.assertEqual(result.snipped_count, 0)

    def test_tool_message_in_middle_is_snipped(self) -> None:
        msgs = [
            _system_msg(),
            _user_msg('task'),
            _tool_msg('big output'),   # 中间段可剪
            _assistant_msg('ok'),      # 尾部 preserve=1 保留
        ]
        result = SNIPPER.snip(msgs, preserve_messages=1)
        self.assertEqual(result.snipped_count, 1)
        self.assertTrue(msgs[2]['content'].startswith('<system-reminder>'))

    def test_tail_messages_are_preserved(self) -> None:
        """尾部 preserve_messages 条不应被剪裁。"""
        msgs = [
            _user_msg('task'),
            _tool_msg('output 1'),   # index 1
            _tool_msg('output 2'),   # index 2 — 尾部 preserve=2，不剪
            _assistant_msg('done'),  # index 3 — 尾部 preserve=2，不剪
        ]
        result = SNIPPER.snip(msgs, preserve_messages=2)
        self.assertEqual(result.snipped_count, 1)   # 只有 index 1 被剪
        self.assertTrue(msgs[1]['content'].startswith('<system-reminder>'))
        # index 2、3 未被剪裁
        self.assertEqual(msgs[2]['content'], 'output 2')
        self.assertEqual(msgs[3]['content'], 'done')

    def test_prefix_system_messages_are_not_snipped(self) -> None:
        """前缀 system 消息不应被剪裁。"""
        msgs = [
            _system_msg('instruction A'),
            _system_msg('instruction B'),
            _tool_msg('output'),   # 可剪
            _user_msg('hi'),       # 尾部
        ]
        result = SNIPPER.snip(msgs, preserve_messages=1)
        # system 消息不被碰，tool 消息被剪
        self.assertEqual(msgs[0]['content'], 'instruction A')
        self.assertEqual(msgs[1]['content'], 'instruction B')
        self.assertEqual(result.snipped_count, 1)

    def test_already_tombstoned_message_not_snipped_again(self) -> None:
        msgs = [_tombstone(), _user_msg('hi')]
        result = SNIPPER.snip(msgs, preserve_messages=0)
        self.assertEqual(result.snipped_count, 0)

    def test_tokens_removed_is_non_negative(self) -> None:
        msgs = [_user_msg('t'), _tool_msg('big content ' * 50), _assistant_msg('ok')]
        result = SNIPPER.snip(msgs, preserve_messages=1)
        self.assertGreaterEqual(result.tokens_removed, 0)


if __name__ == '__main__':
    unittest.main()
