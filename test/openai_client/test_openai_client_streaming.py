"""ISSUE-003 OpenAI-compatible 客户端流式能力测试。

这个测试文件聚焦三件事：
1) SSE 事件是否被正确解析为 StreamEvent。
2) 文本与工具参数增量是否能正确聚合。
3) 流式链路下异常是否保持统一语义。
"""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib import error

from core_contracts.model import ModelConfig, StructuredOutputSpec
from core_contracts.token_usage import TokenUsage
from openai_client.openai_client import (
    OpenAIClient,
    OpenAIConnectionError,
    OpenAIResponseError,
    OpenAITimeoutError,
)


class OpenAIClientStreamingTests(unittest.TestCase):
    """验证 stream/complete_stream 的核心行为。"""

    def setUp(self) -> None:
        self.client = OpenAIClient(
            ModelConfig(
                model='demo-model',
                base_url='http://127.0.0.1:8000/v1',
                api_key='test-token',
                temperature=0.2,
                timeout_seconds=15.0,
            )
        )

    @staticmethod
    def _mock_sse_raw_lines(mock_urlopen: object, lines: list[str]) -> None:
        """直接注入 SSE 原始行，适合构造异常和边界场景。"""
        response = mock_urlopen.return_value.__enter__.return_value
        encoded_lines = [line.encode('utf-8') for line in lines] + [b'']
        iterator = iter(encoded_lines)
        response.readline.side_effect = lambda: next(iterator)

    @classmethod
    def _mock_sse_payloads(
        cls,
        mock_urlopen: object,
        payloads: list[dict],
        *,
        include_done: bool = True,
    ) -> None:
        """根据 payload 快速生成标准 SSE 响应行。"""
        lines: list[str] = []
        for payload in payloads:
            lines.append(f"data: {json.dumps(payload, ensure_ascii=True)}\n")
            lines.append('\n')

        if include_done:
            lines.append('data: [DONE]\n')
            lines.append('\n')

        cls._mock_sse_raw_lines(mock_urlopen, lines)

    # ------------------------------------------------------------------
    # 正常路径
    # ------------------------------------------------------------------

    @patch('openai_client.openai_client.request.urlopen')
    def test_stream_text_and_usage(self, mock_urlopen: object) -> None:
        self._mock_sse_payloads(
            mock_urlopen,
            [
                {'choices': [{'delta': {'content': 'Hello '}}]},
                {'choices': [{'delta': {'content': 'world'}}]},
                {
                    'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
                },
            ],
        )

        events = list(
            self.client.stream(
                messages=[{'role': 'user', 'content': 'say hi'}],
                tools=[],
            )
        )

        req = mock_urlopen.call_args.args[0]
        body = json.loads(req.data.decode('utf-8'))
        self.assertEqual(body['stream'], True)
        self.assertEqual(body['stream_options']['include_usage'], True)

        self.assertEqual(events[0].type, 'message_start')

        content = ''.join(event.delta for event in events if event.type == 'content_delta')
        self.assertEqual(content, 'Hello world')

        stop_events = [event for event in events if event.type == 'message_stop']
        self.assertEqual(len(stop_events), 1)
        self.assertEqual(stop_events[0].finish_reason, 'stop')

        usage_events = [event for event in events if event.type == 'usage']
        self.assertEqual(len(usage_events), 1)
        self.assertEqual(usage_events[0].usage.input_tokens, 3)
        self.assertEqual(usage_events[0].usage.output_tokens, 2)

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_stream_restores_tool_call_arguments(self, mock_urlopen: object) -> None:
        self._mock_sse_payloads(
            mock_urlopen,
            [
                {
                    'choices': [
                        {
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'id': 'call_7',
                                        'function': {
                                            'name': 'read_file',
                                            'arguments': '{"path":"REA',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    'choices': [
                        {
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'function': {
                                            'arguments': 'DME.md"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}],
                    'usage': {'prompt_tokens': 8, 'completion_tokens': 1},
                },
            ],
        )

        result = self.client.complete_stream(
            messages=[{'role': 'user', 'content': 'read readme'}],
            tools=[],
        )

        self.assertEqual(result.content, '')
        self.assertEqual(result.finish_reason, 'tool_calls')
        self.assertEqual(result.usage.input_tokens, 8)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].id, 'call_7')
        self.assertEqual(result.tool_calls[0].name, 'read_file')
        self.assertEqual(result.tool_calls[0].arguments, {'path': 'README.md'})

    @patch('openai_client.openai_client.request.urlopen')
    def test_stream_supports_output_schema(self, mock_urlopen: object) -> None:
        self._mock_sse_payloads(
            mock_urlopen,
            [
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            ],
        )

        schema = StructuredOutputSpec(
            name='result_schema',
            schema={'type': 'object', 'properties': {'answer': {'type': 'string'}}},
            strict=True,
        )

        list(
            self.client.stream(
                messages=[{'role': 'user', 'content': 'return json'}],
                tools=[],
                output_schema=schema,
            )
        )

        req = mock_urlopen.call_args.args[0]
        body = json.loads(req.data.decode('utf-8'))
        self.assertEqual(body['response_format']['json_schema']['name'], 'result_schema')
        self.assertEqual(body['response_format']['json_schema']['strict'], True)

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_stream_without_usage_returns_default_usage(self, mock_urlopen: object) -> None:
        self._mock_sse_payloads(
            mock_urlopen,
            [
                {'choices': [{'delta': {'content': 'ok'}}]},
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            ],
        )

        result = self.client.complete_stream(
            messages=[{'role': 'user', 'content': 'hi'}],
            tools=[],
        )

        self.assertEqual(result.content, 'ok')
        self.assertEqual(result.finish_reason, 'stop')
        self.assertEqual(result.usage, TokenUsage())

    @patch('openai_client.openai_client.request.urlopen')
    def test_stream_done_only_returns_start_event(self, mock_urlopen: object) -> None:
        self._mock_sse_raw_lines(
            mock_urlopen,
            [
                'data: [DONE]\n',
                '\n',
            ],
        )

        events = list(
            self.client.stream(
                messages=[{'role': 'user', 'content': 'hi'}],
                tools=[],
            )
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, 'message_start')

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_stream_accepts_tool_name_from_later_delta(self, mock_urlopen: object) -> None:
        self._mock_sse_payloads(
            mock_urlopen,
            [
                {
                    'choices': [
                        {
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'id': 'call_9',
                                        'function': {
                                            'arguments': '{"path":"REA',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    'choices': [
                        {
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'function': {
                                            'name': 'read_file',
                                            'arguments': 'DME.md"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}],
                },
            ],
        )

        result = self.client.complete_stream(
            messages=[{'role': 'user', 'content': 'read'}],
            tools=[],
        )

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, 'read_file')
        self.assertEqual(result.tool_calls[0].arguments, {'path': 'README.md'})

    # ------------------------------------------------------------------
    # 异常路径
    # ------------------------------------------------------------------

    @patch('openai_client.openai_client.request.urlopen')
    def test_stream_invalid_sse_json_raises_response_error(self, mock_urlopen: object) -> None:
        self._mock_sse_raw_lines(
            mock_urlopen,
            [
                'data: {bad json}\n',
                '\n',
            ],
        )

        with self.assertRaises(OpenAIResponseError):
            list(
                self.client.stream(
                    messages=[{'role': 'user', 'content': 'hi'}],
                    tools=[],
                )
            )

    @patch('openai_client.openai_client.request.urlopen')
    def test_stream_malformed_choices_raises_response_error(self, mock_urlopen: object) -> None:
        self._mock_sse_payloads(
            mock_urlopen,
            [
                {'choices': 'bad'},
            ],
        )

        with self.assertRaises(OpenAIResponseError):
            list(
                self.client.stream(
                    messages=[{'role': 'user', 'content': 'hi'}],
                    tools=[],
                )
            )

    @patch('openai_client.openai_client.request.urlopen')
    def test_stream_http_error_raises_response_error(self, mock_urlopen: object) -> None:
        mock_urlopen.side_effect = error.HTTPError(
            url='http://127.0.0.1:8000/v1/chat/completions',
            code=500,
            msg='Internal Server Error',
            hdrs=None,
            fp=BytesIO(b'backend failed'),
        )

        with self.assertRaises(OpenAIResponseError) as context:
            list(
                self.client.stream(
                    messages=[{'role': 'user', 'content': 'hi'}],
                    tools=[],
                )
            )

        self.assertIn('HTTP 500', str(context.exception))

    @patch('openai_client.openai_client.request.urlopen')
    def test_stream_timeout_raises_timeout_error(self, mock_urlopen: object) -> None:
        mock_urlopen.side_effect = error.URLError(TimeoutError('timed out'))

        with self.assertRaises(OpenAITimeoutError):
            list(
                self.client.stream(
                    messages=[{'role': 'user', 'content': 'hi'}],
                    tools=[],
                )
            )

    @patch('openai_client.openai_client.request.urlopen')
    def test_stream_connection_error_raises_connection_error(self, mock_urlopen: object) -> None:
        mock_urlopen.side_effect = error.URLError('connection refused')

        with self.assertRaises(OpenAIConnectionError):
            list(
                self.client.stream(
                    messages=[{'role': 'user', 'content': 'hi'}],
                    tools=[],
                )
            )


if __name__ == '__main__':
    unittest.main()
