"""最小命令行入口测试。"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core_contracts.budget import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.runtime_policy import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from core_contracts.run_result import AgentRunResult
from core_contracts.token_usage import TokenUsage
from main import main
from core_contracts.session_contracts import AgentSessionSnapshot


def _assert_banner_rendered(testcase: unittest.TestCase, output: str) -> None:
    testcase.assertIn('Tudou Code Agent - Empower Your Coding Journey with AI', output)
    testcase.assertIn('████████╗', output)


class _FakeAgent:
    """拦截 main 中的 agent 调用，避免真实网络请求。"""

    last_client = None
    last_runtime = None
    last_session_store = None
    run_prompts: list[str] = []
    resume_calls: list[tuple[str, str | None]] = []

    @classmethod
    def reset(cls) -> None:
        cls.last_client = None
        cls.last_runtime = None
        cls.last_session_store = None
        cls.run_prompts = []
        cls.resume_calls = []

    def __init__(
        self,
        client,
        workspace_scope,
        execution_policy,
        context_policy,
        permissions,
        budget_config,
        session_paths,
        session_store,
    ) -> None:
        _FakeAgent.last_client = client
        _FakeAgent.last_runtime = SimpleNamespace(
            workspace_scope=workspace_scope,
            execution_policy=execution_policy,
            context_policy=context_policy,
            permissions=permissions,
            budget_config=budget_config,
            session_paths=session_paths,
            max_turns=execution_policy.max_turns,
        )
        _FakeAgent.last_session_store = session_store

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

    def resume(self, prompt: str, session_snapshot) -> AgentRunResult:
        _FakeAgent.resume_calls.append((prompt, session_snapshot.session_id))
        return AgentRunResult(
            final_output=f'resumed:{prompt}',
            turns=2,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
            session_id=session_snapshot.session_id,
        )


def _make_session_manager_cls(load_impl):
    class _FakeSessionManager:
        def __init__(self, directory=None) -> None:
            self.directory = directory

        def load_session(self, session_id: str) -> AgentSessionSnapshot:
            return load_impl(session_id, self.directory)

    return _FakeSessionManager


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
            patch('main.Agent', _FakeAgent),
            patch('builtins.input', side_effect=['你好', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent'])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        self.assertIn('echo:你好', stdout.getvalue())
        self.assertEqual(_FakeAgent.last_client.model_config.model, 'demo-model')
        self.assertEqual(_FakeAgent.last_client.model_config.api_key, 'demo-key')
        self.assertEqual(_FakeAgent.last_client.model_config.base_url, 'http://127.0.0.1:9000/v1')

    def test_main_missing_api_key_returns_error(self) -> None:
        with patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': ''}, clear=False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(['agent'])

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
            patch('main.Agent', _FakeAgent),
            patch('builtins.input', side_effect=['/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent', '--allow-file-write', '--allow-shell', '--allow-destructive-shell'])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_file_write)
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_shell_commands)
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_destructive_shell_commands)

    def test_main_session_directory_override_applies_to_runtime_and_store(self) -> None:
        target_directory = (Path.cwd() / '.tmp-cli-session-dir').resolve()

        with (
            patch.dict(
                os.environ,
                {
                    'OPENAI_MODEL': 'demo-model',
                    'OPENAI_API_KEY': 'demo-key',
                },
                clear=False,
            ),
            patch('main.Agent', _FakeAgent),
            patch('builtins.input', side_effect=['/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent', '--session-directory', str(target_directory)])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        self.assertEqual(_FakeAgent.last_runtime.session_paths.session_directory, target_directory)
        self.assertEqual(_FakeAgent.last_session_store.directory, target_directory)

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
                code = main(['agent', '--allow-destructive-shell'])

        self.assertEqual(code, 2)
        self.assertIn('allow_destructive_shell requires --allow-shell', stderr.getvalue())

    # ------------------------------------------------------------------
    # ISSUE-013 agent-resume CLI 路径
    # ------------------------------------------------------------------

    def _make_session_snapshot(self) -> AgentSessionSnapshot:
        """构造最小可用的持久化会话对象，用于 resume 测试。"""
        return AgentSessionSnapshot(
            session_id='resume-test-001',
            model_config=ModelConfig(
                model='demo-model',
                base_url='http://127.0.0.1:9000/v1',
                api_key='demo-key',
            ),
            workspace_scope=WorkspaceScope(cwd=Path('.').resolve()),
            execution_policy=ExecutionPolicy(),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            budget_config=BudgetConfig(),
            session_paths=SessionPaths(),
            messages=({'role': 'user', 'content': '历史问题'},),
        )

    def test_agent_resume_triggers_resume_not_run(self) -> None:
        """agent-resume 应触发 AgentSessionStore.load() + agent.resume，而非 agent.run。"""
        stored = self._make_session_snapshot()

        def _load_snapshot(session_id, directory):
            return stored

        with (
            patch('main.SessionGateway', _make_session_manager_cls(_load_snapshot)),
            patch('main.Agent', _FakeAgent),
            patch('builtins.input', side_effect=['续跑问题', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-resume', 'resume-test-001'])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        self.assertIn('resumed:续跑问题', stdout.getvalue())
        self.assertEqual(_FakeAgent.resume_calls, [('续跑问题', 'resume-test-001')])

    def test_agent_resume_can_override_stored_config(self) -> None:
        stored = AgentSessionSnapshot(
            session_id='resume-test-001',
            model_config=ModelConfig(
                model='stored-model',
                base_url='http://127.0.0.1:9000/v1',
                api_key='stored-key',
            ),
            workspace_scope=WorkspaceScope(cwd=Path('.').resolve()),
            execution_policy=ExecutionPolicy(max_turns=3),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            budget_config=BudgetConfig(),
            session_paths=SessionPaths(),
            messages=({'role': 'user', 'content': '历史问题'},),
        )

        def _load_snapshot(session_id, directory):
            return stored

        with (
            patch('main.SessionGateway', _make_session_manager_cls(_load_snapshot)),
            patch('main.Agent', _FakeAgent),
            patch('builtins.input', side_effect=['/exit']),
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
                ])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        self.assertEqual(_FakeAgent.last_client.model_config.model, 'override-model')
        self.assertEqual(_FakeAgent.last_client.model_config.api_key, 'override-key')
        self.assertEqual(_FakeAgent.last_client.model_config.temperature, 0.7)
        self.assertEqual(_FakeAgent.last_runtime.max_turns, 9)
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_shell_commands)
        self.assertFalse(_FakeAgent.last_runtime.permissions.allow_file_write)

    def test_agent_resume_session_directory_override_applies_to_runtime_and_store(self) -> None:
        stored = self._make_session_snapshot()
        target_directory = (Path.cwd() / '.tmp-resume-session-dir').resolve()
        load_directories: list[Path | None] = []

        def _load_snapshot(session_id, directory):
            load_directories.append(directory)
            return stored

        with (
            patch('main.SessionGateway', _make_session_manager_cls(_load_snapshot)),
            patch('main.Agent', _FakeAgent),
            patch('builtins.input', side_effect=['/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main([
                    'agent-resume',
                    '--session-directory', str(target_directory),
                    'resume-test-001',
                ])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        self.assertEqual(load_directories, [target_directory])
        self.assertEqual(_FakeAgent.last_runtime.session_paths.session_directory, target_directory)
        self.assertEqual(_FakeAgent.last_session_store.directory, target_directory)

    def test_agent_resume_trailing_flag_after_session_id_applies(self) -> None:
        stored = self._make_session_snapshot()

        def _load_snapshot(session_id, directory):
            return stored

        with (
            patch('main.SessionGateway', _make_session_manager_cls(_load_snapshot)),
            patch('main.Agent', _FakeAgent),
            patch('builtins.input', side_effect=['请把春江花月夜全文写入文件', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main([
                    'agent-resume',
                    'resume-test-001',
                    '--allow-file-write',
                ])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        self.assertEqual(
            _FakeAgent.resume_calls,
            [('请把春江花月夜全文写入文件', 'resume-test-001')],
        )
        self.assertTrue(_FakeAgent.last_runtime.permissions.allow_file_write)

    def test_agent_resume_missing_session_returns_error(self) -> None:
        """session 文件不存在时应返回退出码 2 并输出可读错误。"""
        def _load_snapshot(session_id, directory):
            raise ValueError('Session not found: xyz')

        with patch('main.SessionGateway', _make_session_manager_cls(_load_snapshot)):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(['agent-resume', 'xyz'])

        self.assertEqual(code, 2)
        self.assertIn('Session not found', stderr.getvalue())

    def test_agent_subcommand_still_runs_normally(self) -> None:
        """agent 子命令应走 run 路径，并支持多轮输入。"""
        with (
            patch.dict(
                os.environ,
                {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'},
                clear=False,
            ),
            patch('main.Agent', _FakeAgent),
            patch('builtins.input', side_effect=['普通问题', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent'])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        self.assertIn('echo:普通问题', stdout.getvalue())
        self.assertEqual(_FakeAgent.run_prompts, ['普通问题'])
        self.assertEqual(_FakeAgent.resume_calls, [])


if __name__ == '__main__':
    unittest.main()

