"""最小命令行入口测试。"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from src.contract_types import AgentRunResult, TokenUsage
from src.main import main


class _FakeAgent:
    """拦截 main 中的 agent 调用，避免真实网络请求。"""

    last_client = None
    last_runtime = None

    def __init__(self, client, runtime_config) -> None:
        _FakeAgent.last_client = client
        _FakeAgent.last_runtime = runtime_config

    def run(self, prompt: str) -> AgentRunResult:
        return AgentRunResult(
            final_output=f'echo:{prompt}',
            turns=1,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
        )


class MainEntryTests(unittest.TestCase):
    """验证 main 入口的配置解析与执行路径。"""

    def test_main_uses_env_values(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    'OPENAI_MODEL': 'demo-model',
                    'OPENAI_API_KEY': 'demo-key',
                    'OPENAI_BASE_URL': 'http://127.0.0.1:9000/v1',
                },
                clear=False,
            ),
            patch('src.main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['你好'])

        self.assertEqual(code, 0)
        self.assertIn('echo:你好', stdout.getvalue())
        self.assertEqual(_FakeAgent.last_client.config.model, 'demo-model')
        self.assertEqual(_FakeAgent.last_client.config.api_key, 'demo-key')
        self.assertEqual(_FakeAgent.last_client.config.base_url, 'http://127.0.0.1:9000/v1')

    def test_main_missing_api_key_returns_error(self) -> None:
        with patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': ''}, clear=False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(['你好'])

        self.assertEqual(code, 2)
        self.assertIn('Missing required api_key', stderr.getvalue())

    def test_main_flags_override_runtime_permissions(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    'OPENAI_MODEL': 'demo-model',
                    'OPENAI_API_KEY': 'demo-key',
                },
                clear=False,
            ),
            patch('src.main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['--allow-file-write', '--allow-shell', '--allow-destructive-shell', '测试'])

        self.assertEqual(code, 0)
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_file_write)
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_shell_commands)
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_destructive_shell_commands)

    def test_main_destructive_shell_requires_shell_flag(self) -> None:
        with patch.dict(
            os.environ,
            {
                'OPENAI_MODEL': 'demo-model',
                'OPENAI_API_KEY': 'demo-key',
            },
            clear=False,
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(['--allow-destructive-shell', '测试'])

        self.assertEqual(code, 2)
        self.assertIn('allow_destructive_shell requires --allow-shell', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
