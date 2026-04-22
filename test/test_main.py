"""最小命令行入口测试。"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

from src.contract_types import AgentRunResult, AgentRuntimeConfig, ModelConfig, TokenUsage
from src.main import main
from src.session import StoredAgentSession


class _FakeAgent:
    """拦截 main 中的 agent 调用，避免真实网络请求。"""

    last_client = None
    last_runtime = None
    last_resume_prompt: str | None = None
    last_resume_stored = None

    def __init__(self, client, runtime_config) -> None:
        _FakeAgent.last_client = client
        _FakeAgent.last_runtime = runtime_config
        _FakeAgent.last_resume_prompt = None
        _FakeAgent.last_resume_stored = None

    def run(self, prompt: str) -> AgentRunResult:
        return AgentRunResult(
            final_output=f'echo:{prompt}',
            turns=1,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
        )

    def resume(self, prompt: str, stored_session) -> AgentRunResult:
        _FakeAgent.last_resume_prompt = prompt
        _FakeAgent.last_resume_stored = stored_session
        return AgentRunResult(
            final_output=f'resumed:{prompt}',
            turns=2,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
            session_id=stored_session.session_id,
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

    # ------------------------------------------------------------------
    # ISSUE-008 Resume CLI 路径
    # ------------------------------------------------------------------

    def _make_stored_session(self) -> StoredAgentSession:
        """构造最小可用的持久化会话对象，用于 resume 测试。"""
        return StoredAgentSession(
            session_id='resume-test-001',
            model_config=ModelConfig(
                model='demo-model',
                base_url='http://127.0.0.1:9000/v1',
                api_key='demo-key',
            ),
            runtime_config=AgentRuntimeConfig(cwd='.'),
            messages=({'role': 'user', 'content': '历史问题'},),
        )

    def test_main_session_id_triggers_resume_not_run(self) -> None:
        """--session-id 应触发 load_agent_session + agent.resume，而非 agent.run。"""
        stored = self._make_stored_session()
        with (
            patch('src.main.load_agent_session', return_value=stored),
            patch('src.main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['--session-id', 'resume-test-001', '续跑问题'])

        self.assertEqual(code, 0)
        self.assertIn('resumed:续跑问题', stdout.getvalue())
        self.assertEqual(_FakeAgent.last_resume_prompt, '续跑问题')
        self.assertIs(_FakeAgent.last_resume_stored, stored)

    def test_main_resume_missing_session_returns_error(self) -> None:
        """session 文件不存在时应返回退出码 2 并输出可读错误。"""
        with patch('src.main.load_agent_session', side_effect=ValueError('Session not found: xyz')):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(['--session-id', 'xyz', '续跑'])

        self.assertEqual(code, 2)
        self.assertIn('Session not found', stderr.getvalue())

    def test_main_without_session_id_still_runs_normally(self) -> None:
        """无 --session-id 时保持原有 run 路径不回归。"""
        with (
            patch.dict(
                os.environ,
                {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'},
                clear=False,
            ),
            patch('src.main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['普通问题'])

        self.assertEqual(code, 0)
        self.assertIn('echo:普通问题', stdout.getvalue())
        # resume 方法不应被调用
        self.assertIsNone(_FakeAgent.last_resume_prompt)


if __name__ == '__main__':
    unittest.main()
