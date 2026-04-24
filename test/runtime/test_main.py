"""最小命令行入口测试。"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from core_contracts.config import AgentRuntimeConfig, ModelConfig
from core_contracts.result import AgentRunResult
from core_contracts.usage import TokenUsage
from main import main
from session.session_contracts import StoredAgentSession


class _FakeAgent:
    """拦截 main 中的 agent 调用，避免真实网络请求。"""

    last_client = None
    last_runtime = None
    run_prompts: list[str] = []
    resume_calls: list[tuple[str, str | None]] = []

    @classmethod
    def reset(cls) -> None:
        cls.last_client = None
        cls.last_runtime = None
        cls.run_prompts = []
        cls.resume_calls = []

    def __init__(self, client, runtime_config) -> None:
        _FakeAgent.last_client = client
        _FakeAgent.last_runtime = runtime_config

    def run(self, prompt: str) -> AgentRunResult:
        _FakeAgent.run_prompts.append(prompt)
        return AgentRunResult(
            final_output=f'echo:{prompt}',
            turns=1,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
            session_id='new-session-001',
        )

    def resume(self, prompt: str, stored_session) -> AgentRunResult:
        _FakeAgent.resume_calls.append((prompt, stored_session.session_id))
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

    def setUp(self) -> None:
        _FakeAgent.reset()

    def test_main_requires_subcommand(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main([])

        self.assertEqual(code, 2)
        self.assertIn('the following arguments are required: command', stderr.getvalue())

    def test_main_rejects_legacy_top_level_prompt(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(['你好'])

        self.assertEqual(code, 2)
        self.assertIn('invalid choice', stderr.getvalue())

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
            patch('main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent', '你好'])

        self.assertEqual(code, 0)
        self.assertIn('echo:你好', stdout.getvalue())
        self.assertEqual(_FakeAgent.last_client.config.model, 'demo-model')
        self.assertEqual(_FakeAgent.last_client.config.api_key, 'demo-key')
        self.assertEqual(_FakeAgent.last_client.config.base_url, 'http://127.0.0.1:9000/v1')

    def test_main_missing_api_key_returns_error(self) -> None:
        with patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': ''}, clear=False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(['agent', '你好'])

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
            patch('main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent', '--allow-file-write', '--allow-shell', '--allow-destructive-shell', '测试'])

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
                code = main(['agent', '--allow-destructive-shell', '测试'])

        self.assertEqual(code, 2)
        self.assertIn('allow_destructive_shell requires --allow-shell', stderr.getvalue())

    # ------------------------------------------------------------------
    # ISSUE-013 agent-resume CLI 路径
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
            runtime_config=AgentRuntimeConfig(cwd=Path('.').resolve()),
            messages=({'role': 'user', 'content': '历史问题'},),
        )

    def test_agent_resume_triggers_resume_not_run(self) -> None:
        """agent-resume 应触发 load_agent_session + agent.resume，而非 agent.run。"""
        stored = self._make_stored_session()
        with (
            patch('main.load_agent_session', return_value=stored),
            patch('main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-resume', 'resume-test-001', '续跑问题'])

        self.assertEqual(code, 0)
        self.assertIn('resumed:续跑问题', stdout.getvalue())
        self.assertEqual(_FakeAgent.resume_calls, [('续跑问题', 'resume-test-001')])

    def test_agent_resume_can_override_stored_config(self) -> None:
        stored = StoredAgentSession(
            session_id='resume-test-001',
            model_config=ModelConfig(
                model='stored-model',
                base_url='http://127.0.0.1:9000/v1',
                api_key='stored-key',
            ),
            runtime_config=AgentRuntimeConfig(
                cwd=Path('.').resolve(),
                max_turns=3,
            ),
            messages=({'role': 'user', 'content': '历史问题'},),
        )
        with (
            patch('main.load_agent_session', return_value=stored),
            patch('main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main([
                    'agent-resume',
                    '--model', 'override-model',
                    '--api-key', 'override-key',
                    '--temperature', '0.7',
                    '--max-turns', '9',
                    '--allow-shell',
                    '--no-allow-file-write',
                    'resume-test-001',
                    '继续任务',
                ])

        self.assertEqual(code, 0)
        self.assertEqual(_FakeAgent.last_client.config.model, 'override-model')
        self.assertEqual(_FakeAgent.last_client.config.api_key, 'override-key')
        self.assertEqual(_FakeAgent.last_client.config.temperature, 0.7)
        self.assertEqual(_FakeAgent.last_runtime.max_turns, 9)
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_shell_commands)
        self.assertFalse(_FakeAgent.last_runtime.permissions.allow_file_write)

    def test_agent_resume_trailing_flag_after_prompt_still_applies(self) -> None:
        stored = self._make_stored_session()
        with (
            patch('main.load_agent_session', return_value=stored),
            patch('main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main([
                    'agent-resume',
                    'resume-test-001',
                    '请把春江花月夜全文写入文件',
                    '--allow-file-write',
                ])

        self.assertEqual(code, 0)
        self.assertEqual(
            _FakeAgent.resume_calls,
            [('请把春江花月夜全文写入文件', 'resume-test-001')],
        )
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_file_write)

    def test_agent_resume_missing_session_returns_error(self) -> None:
        """session 文件不存在时应返回退出码 2 并输出可读错误。"""
        with patch('main.load_agent_session', side_effect=ValueError('Session not found: xyz')):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(['agent-resume', 'xyz', '续跑'])

        self.assertEqual(code, 2)
        self.assertIn('Session not found', stderr.getvalue())

    def test_agent_subcommand_still_runs_normally(self) -> None:
        """agent 子命令应走 run 路径。"""
        with (
            patch.dict(
                os.environ,
                {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'},
                clear=False,
            ),
            patch('main.LocalCodingAgent', _FakeAgent),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent', '普通问题'])

        self.assertEqual(code, 0)
        self.assertIn('echo:普通问题', stdout.getvalue())
        self.assertEqual(_FakeAgent.run_prompts, ['普通问题'])
        self.assertEqual(_FakeAgent.resume_calls, [])


if __name__ == '__main__':
    unittest.main()
