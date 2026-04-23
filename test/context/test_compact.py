"""ISSUE-011 compact 单元测试。"""

from __future__ import annotations

import unittest

from src.context.compact import (
    apply_compact_summary,
    build_compact_request_messages,
    compact_conversation,
    format_compact_summary,
    is_context_length_error,
    should_auto_compact,
)
from src.core_contracts import ModelConfig, OneTurnResponse, TokenUsage
from src.openai_client.openai_client import OpenAIClient, OpenAIConnectionError, OpenAIResponseError


class _FakeCompactClient(OpenAIClient):
    def __init__(self, responses: list[OneTurnResponse | Exception]) -> None:
        super().__init__(
            ModelConfig(
                model='fake-model',
                base_url='http://127.0.0.1:1/v1',
                api_key='fake-key',
                temperature=0.0,
            )
        )
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete(self, messages, tools=None, *, output_schema=None):  # type: ignore[override]
        self.calls.append(
            {
                'messages': [dict(item) for item in messages],
                'tools': list(tools or []),
                'output_schema': output_schema,
            }
        )
        if not self._responses:
            raise AssertionError('No prepared response left for test')
        current = self._responses.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class CompactThresholdTests(unittest.TestCase):
    def test_threshold_none_disables_auto_compact(self) -> None:
        self.assertFalse(should_auto_compact(100, None))

    def test_threshold_triggers_at_or_above_limit(self) -> None:
        self.assertTrue(should_auto_compact(100, 100))
        self.assertTrue(should_auto_compact(101, 100))
        self.assertFalse(should_auto_compact(99, 100))


class ContextLengthErrorTests(unittest.TestCase):
    def test_http_413_is_always_context_length_error(self) -> None:
        exc = OpenAIResponseError('HTTP 413 from model backend', status_code=413, detail='too large')
        self.assertTrue(is_context_length_error(exc))

    def test_keyword_based_context_length_error_detection(self) -> None:
        exc = OpenAIResponseError(
            'HTTP 400 from model backend: maximum context length exceeded',
            status_code=400,
            detail='maximum context length exceeded',
        )
        self.assertTrue(is_context_length_error(exc))

    def test_non_context_error_is_not_detected(self) -> None:
        self.assertFalse(is_context_length_error(OpenAIConnectionError('network down')))


class CompactPromptTests(unittest.TestCase):
    def test_build_compact_request_excludes_preserved_tail(self) -> None:
        messages = [
            {'role': 'system', 'content': 'rule'},
            {'role': 'user', 'content': 'old request'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'latest request'},
        ]

        request_messages = build_compact_request_messages(messages, preserve_messages=1)

        self.assertIsNotNone(request_messages)
        assert request_messages is not None
        self.assertEqual(len(request_messages), 2)
        self.assertIn('old request', request_messages[1]['content'])
        self.assertIn('old answer', request_messages[1]['content'])
        self.assertNotIn('latest request', request_messages[1]['content'])

    def test_format_compact_summary_collapses_excess_blank_lines(self) -> None:
        self.assertEqual(format_compact_summary('A\n\n\nB\n'), 'A\n\nB')


class ApplyCompactSummaryTests(unittest.TestCase):
    def test_apply_compact_summary_preserves_prefix_and_tail(self) -> None:
        messages = [
            {'role': 'system', 'content': 'rule'},
            {'role': 'user', 'content': 'old request'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'latest request'},
        ]

        result = apply_compact_summary(messages, 'Goal: continue work', preserve_messages=1)

        self.assertTrue(result.compacted)
        self.assertEqual(messages[0]['content'], 'rule')
        self.assertTrue(messages[1]['content'].startswith('<system-reminder>\nEarlier conversation history'))
        self.assertTrue(messages[2]['content'].startswith('<system-reminder>\nCompact summary of earlier conversation:'))
        self.assertEqual(messages[-1]['content'], 'latest request')
        self.assertGreaterEqual(result.tokens_removed, 0)

    def test_apply_compact_summary_requires_middle_slice(self) -> None:
        messages = [{'role': 'user', 'content': 'latest request'}]
        result = apply_compact_summary(messages, 'Goal', preserve_messages=1)
        self.assertFalse(result.compacted)
        self.assertEqual(result.error, 'Not enough messages to compact')


class CompactConversationTests(unittest.TestCase):
    def test_compact_conversation_rewrites_messages_and_tracks_usage(self) -> None:
        messages = [
            {'role': 'user', 'content': 'old request'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'tool', 'name': 'read_file', 'tool_call_id': 't1', 'content': 'file contents'},
            {'role': 'user', 'content': 'latest request'},
        ]
        client = _FakeCompactClient([
            OneTurnResponse(
                content='User goal: fix file\nNext: continue from latest request',
                finish_reason='stop',
                usage=TokenUsage(input_tokens=7, output_tokens=3),
            )
        ])

        result = compact_conversation(client, messages, preserve_messages=1)

        self.assertTrue(result.compacted)
        self.assertEqual(result.usage.input_tokens, 7)
        self.assertEqual(client.calls[0]['tools'], [])
        self.assertEqual(messages[-1]['content'], 'latest request')
        self.assertIn('User goal: fix file', messages[1]['content'])

    def test_compact_conversation_returns_error_on_empty_summary(self) -> None:
        messages = [
            {'role': 'user', 'content': 'old request'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'latest request'},
        ]
        client = _FakeCompactClient([
            OneTurnResponse(content='  ', finish_reason='stop', usage=TokenUsage(input_tokens=2, output_tokens=1))
        ])

        result = compact_conversation(client, messages, preserve_messages=1)

        self.assertFalse(result.compacted)
        self.assertEqual(result.error, 'Compact model returned empty summary')


if __name__ == '__main__':
    unittest.main()