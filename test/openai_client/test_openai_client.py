"""ISSUE-002 OpenAI-compatible 非流式客户端测试。

这个测试文件专注验证三类行为：
1) 请求构造是否符合预期。
2) 响应解析是否稳定覆盖文本、tool_calls、usage 变体。
3) 异常是否统一映射为客户端自定义异常类型。
"""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib import error

from core_contracts.model import ModelConfig, StructuredOutputSpec
from core_contracts.primitives import TokenUsage
from openai_client.openai_client import (
    OpenAIClient,
    OpenAIConnectionError,
    OpenAIResponseError,
    OpenAITimeoutError,
)


class OpenAIClientTests(unittest.TestCase):
    """验证非流式客户端的请求、解析与异常语义。"""

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
    def _mock_json_response(mock_urlopen: object, payload: dict) -> None:
        """为 urlopen mock 注入 JSON 响应体。"""
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            payload
        ).encode('utf-8')

    @staticmethod
    def _build_single_choice_payload(
        *,
        message: dict,
        finish_reason: str = 'stop',
        usage: dict | None = None,
    ) -> dict:
        """构造单 choice 的标准响应，减少测试样板代码。"""
        payload = {
            'choices': [
                {
                    'message': message,
                    'finish_reason': finish_reason,
                }
            ],
        }
        if usage is not None:
            payload['usage'] = usage
        return payload

    # ------------------------------------------------------------------
    # 正常路径
    # ------------------------------------------------------------------

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_text_response(self, mock_urlopen: object) -> None:
        self._mock_json_response(
            mock_urlopen,
            self._build_single_choice_payload(
                message={'content': 'hello world'},
                usage={'prompt_tokens': 3, 'completion_tokens': 5},
            ),
        )

        result = self.client.complete(
            messages=[{'role': 'user', 'content': 'hi'}],
            tools=[
                {
                    'type': 'function',
                    'function': {'name': 'read_file', 'parameters': {'type': 'object'}},
                }
            ],
        )

        req = mock_urlopen.call_args.args[0]
        body = json.loads(req.data.decode('utf-8'))

        self.assertEqual(body['model'], 'demo-model')
        self.assertEqual(body['messages'][0]['role'], 'user')
        self.assertEqual(body['tool_choice'], 'auto')
        self.assertEqual(result.content, 'hello world')
        self.assertEqual(result.finish_reason, 'stop')
        self.assertEqual(result.usage.input_tokens, 3)
        self.assertEqual(result.usage.output_tokens, 5)

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_supports_output_schema(self, mock_urlopen: object) -> None:
        self._mock_json_response(
            mock_urlopen,
            self._build_single_choice_payload(message={'content': 'ok'}, usage={}),
        )

        schema = StructuredOutputSpec(
            name='result_schema',
            schema={'type': 'object', 'properties': {'answer': {'type': 'string'}}},
            strict=True,
        )
        self.client.complete(
            messages=[{'role': 'user', 'content': 'return json'}],
            tools=[],
            output_schema=schema,
        )

        req = mock_urlopen.call_args.args[0]
        body = json.loads(req.data.decode('utf-8'))
        self.assertEqual(body['response_format']['json_schema']['name'], 'result_schema')
        self.assertEqual(body['response_format']['json_schema']['strict'], True)

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_parses_tool_calls(self, mock_urlopen: object) -> None:
        self._mock_json_response(
            mock_urlopen,
            self._build_single_choice_payload(
                message={
                    'content': '',
                    'tool_calls': [
                        {
                            'id': 'call_9',
                            'type': 'function',
                            'function': {
                                'name': 'read_file',
                                'arguments': '{"path": "README.md"}',
                            },
                        }
                    ],
                },
                finish_reason='tool_calls',
                usage={'input_tokens': 10, 'output_tokens': 0},
            ),
        )

        result = self.client.complete(
            messages=[{'role': 'user', 'content': 'read readme'}],
            tools=[],
        )

        self.assertEqual(result.finish_reason, 'tool_calls')
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].id, 'call_9')
        self.assertEqual(result.tool_calls[0].name, 'read_file')
        self.assertEqual(result.tool_calls[0].arguments, {'path': 'README.md'})

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_uses_first_choice_only(self, mock_urlopen: object) -> None:
        self._mock_json_response(
            mock_urlopen,
            {
                'choices': [
                    {
                        'message': {'content': 'first answer'},
                        'finish_reason': 'stop',
                    },
                    {
                        'message': {'content': 'second answer'},
                        'finish_reason': 'stop',
                    },
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
        )

        result = self.client.complete(
            messages=[{'role': 'user', 'content': 'pick first'}],
            tools=[],
        )

        self.assertEqual(result.content, 'first answer')

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_usage_variant_fields(self, mock_urlopen: object) -> None:
        self._mock_json_response(
            mock_urlopen,
            self._build_single_choice_payload(
                message={'content': 'ok'},
                usage={
                    'prompt_eval_count': 11,
                    'eval_count': 7,
                    'completion_tokens_details': {'reasoning_tokens': 3},
                },
            ),
        )

        result = self.client.complete(
            messages=[{'role': 'user', 'content': 'hi'}],
            tools=[],
        )

        self.assertEqual(result.usage.input_tokens, 11)
        self.assertEqual(result.usage.output_tokens, 7)
        self.assertEqual(result.usage.reasoning_tokens, 3)

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_missing_usage_returns_default(self, mock_urlopen: object) -> None:
        self._mock_json_response(
            mock_urlopen,
            self._build_single_choice_payload(message={'content': 'ok'}),
        )

        result = self.client.complete(
            messages=[{'role': 'user', 'content': 'hi'}],
            tools=[],
        )

        self.assertEqual(result.usage, TokenUsage())

    # ------------------------------------------------------------------
    # 异常路径
    # ------------------------------------------------------------------

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_http_error_raises_response_error(self, mock_urlopen: object) -> None:
        mock_urlopen.side_effect = error.HTTPError(
            url='http://127.0.0.1:8000/v1/chat/completions',
            code=500,
            msg='Internal Server Error',
            hdrs=None,
            fp=BytesIO(b'backend failed'),
        )

        with self.assertRaises(OpenAIResponseError) as context:
            self.client.complete(
                messages=[{'role': 'user', 'content': 'hi'}],
                tools=[],
            )

        self.assertIn('HTTP 500', str(context.exception))
        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(context.exception.detail, 'backend failed')

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_timeout_raises_timeout_error(self, mock_urlopen: object) -> None:
        mock_urlopen.side_effect = error.URLError(TimeoutError('timed out'))

        with self.assertRaises(OpenAITimeoutError):
            self.client.complete(
                messages=[{'role': 'user', 'content': 'hi'}],
                tools=[],
            )

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_connection_error_raises_connection_error(self, mock_urlopen: object) -> None:
        mock_urlopen.side_effect = error.URLError('connection refused')

        with self.assertRaises(OpenAIConnectionError):
            self.client.complete(
                messages=[{'role': 'user', 'content': 'hi'}],
                tools=[],
            )

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_invalid_response_raises_response_error(self, mock_urlopen: object) -> None:
        self._mock_json_response(mock_urlopen, {'choices': []})

        with self.assertRaises(OpenAIResponseError):
            self.client.complete(
                messages=[{'role': 'user', 'content': 'hi'}],
                tools=[],
            )

    @patch('openai_client.openai_client.request.urlopen')
    def test_complete_malformed_choices_type_raises_response_error(self, mock_urlopen: object) -> None:
        self._mock_json_response(mock_urlopen, {'choices': 'bad'})

        with self.assertRaises(OpenAIResponseError):
            self.client.complete(
                messages=[{'role': 'user', 'content': 'hi'}],
                tools=[],
            )


if __name__ == '__main__':
    unittest.main()
