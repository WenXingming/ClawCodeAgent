"""ISSUE-011 Compactor 单元测试。"""

from __future__ import annotations

import unittest

from context.compactor import Compactor
from core_contracts.model import ModelConfig
from core_contracts.openai_contracts import ModelClient, ModelConnectionError, ModelResponseError
from core_contracts.protocol import OneTurnResponse
from core_contracts.token_usage import TokenUsage
from openai_client.openai_client import OpenAIClient


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


def _compactor(client: ModelClient | None = None) -> Compactor:
    return Compactor(client or _FakeCompactClient([]))


class CompactThresholdTests(unittest.TestCase):
    def test_threshold_none_disables_auto_compact(self) -> None:
        self.assertFalse(_compactor().should_auto_compact(100, None))

    def test_threshold_triggers_at_or_above_limit(self) -> None:
        compactor = _compactor()
        self.assertTrue(compactor.should_auto_compact(100, 100))
        self.assertTrue(compactor.should_auto_compact(101, 100))
        self.assertFalse(compactor.should_auto_compact(99, 100))


class ContextLengthErrorTests(unittest.TestCase):
    def test_http_413_is_always_context_length_error(self) -> None:
        exc = ModelResponseError('HTTP 413 from model backend', status_code=413, detail='too large')
        self.assertTrue(_compactor().is_context_length_error(exc))

    def test_keyword_based_context_length_error_detection(self) -> None:
        exc = ModelResponseError(
            'HTTP 400 from model backend: maximum context length exceeded',
            status_code=400,
            detail='maximum context length exceeded',
        )
        self.assertTrue(_compactor().is_context_length_error(exc))

    def test_non_context_error_is_not_detected(self) -> None:
        self.assertFalse(_compactor().is_context_length_error(ModelConnectionError('network down')))


class CompactWorkflowTests(unittest.TestCase):
    def test_compact_request_excludes_preserved_tail(self) -> None:
        messages = [
            {'role': 'system', 'content': 'rule'},
            {'role': 'user', 'content': 'old request'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'latest request'},
        ]
        client = _FakeCompactClient([
            OneTurnResponse(content='Summary', finish_reason='stop', usage=TokenUsage())
        ])

        result = Compactor(client).compact(messages, preserve_messages=1)

        self.assertTrue(result.compacted)
        request_messages = client.calls[0]['messages']
        self.assertEqual(len(request_messages), 2)
        self.assertIn('old request', request_messages[1]['content'])
        self.assertIn('old answer', request_messages[1]['content'])
        self.assertNotIn('latest request', request_messages[1]['content'])

    def test_compact_collapses_excess_blank_lines_in_summary(self) -> None:
        messages = [
            {'role': 'system', 'content': 'rule'},
            {'role': 'user', 'content': 'old request'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'latest request'},
        ]
        client = _FakeCompactClient([
            OneTurnResponse(content='A\n\n\nB\n', finish_reason='stop', usage=TokenUsage())
        ])

        result = Compactor(client).compact(messages, preserve_messages=1)

        self.assertEqual(result.summary_text, 'A\n\nB')
        self.assertIn('A\n\nB', messages[2]['content'])

    def test_compact_preserves_prefix_and_tail(self) -> None:
        messages = [
            {'role': 'system', 'content': 'rule'},
            {'role': 'user', 'content': 'old request'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'latest request'},
        ]
        client = _FakeCompactClient([
            OneTurnResponse(content='Goal: continue work', finish_reason='stop', usage=TokenUsage())
        ])

        result = Compactor(client).compact(messages, preserve_messages=1)

        self.assertTrue(result.compacted)
        self.assertEqual(messages[0]['content'], 'rule')
        self.assertTrue(messages[1]['content'].startswith('<system-reminder>\nEarlier conversation history'))
        self.assertTrue(messages[2]['content'].startswith('<system-reminder>\nCompact summary of earlier conversation:'))
        self.assertEqual(messages[-1]['content'], 'latest request')
        self.assertGreaterEqual(result.tokens_removed, 0)

    def test_compact_requires_middle_slice(self) -> None:
        messages = [{'role': 'user', 'content': 'latest request'}]
        result = _compactor().compact(messages, preserve_messages=1)
        self.assertFalse(result.compacted)
        self.assertEqual(result.error, 'Not enough messages to compact')


class CompactConversationTests(unittest.TestCase):
    def test_compact_rewrites_messages_and_tracks_usage(self) -> None:
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

        result = Compactor(client).compact(messages, preserve_messages=1)

        self.assertTrue(result.compacted)
        self.assertEqual(result.usage.input_tokens, 7)
        self.assertEqual(client.calls[0]['tools'], [])
        self.assertEqual(messages[-1]['content'], 'latest request')
        self.assertIn('User goal: fix file', messages[1]['content'])

    def test_compact_returns_error_on_empty_summary(self) -> None:
        messages = [
            {'role': 'user', 'content': 'old request'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'latest request'},
        ]
        client = _FakeCompactClient([
            OneTurnResponse(content='  ', finish_reason='stop', usage=TokenUsage(input_tokens=2, output_tokens=1))
        ])

        result = Compactor(client).compact(messages, preserve_messages=1)

        self.assertFalse(result.compacted)
        self.assertEqual(result.error, 'Compact model returned empty summary')


if __name__ == '__main__':
    unittest.main()