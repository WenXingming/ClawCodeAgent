"""ISSUE-013 交互式 chat CLI 测试。"""

from __future__ import annotations

import io
import os
import re
import unittest
from contextlib import redirect_stdout
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
from session.session_snapshot import AgentSessionSnapshot


def _assert_banner_rendered(testcase: unittest.TestCase, output: str) -> None:
    testcase.assertIn('Tudou Code Agent - Empower Your Coding Journey with AI', output)
    testcase.assertIn('████████╗', output)
    testcase.assertIn(
        'Environment loaded: 1 MCP server, 2 plugins, 1 hook policy, 1 search provider',
        output,
    )


def _assert_exit_summary_rendered(
    testcase: unittest.TestCase,
    output: str,
    *,
    session_id: str | None,
    show_resume_hint: bool,
) -> None:
    testcase.assertIn('Agent powering down. Goodbye!', output)
    testcase.assertIn('Interaction Summary', output)
    testcase.assertIn('Tool Calls:', output)
    testcase.assertIn('Success Rate:', output)
    testcase.assertIn('Wall Time:', output)
    testcase.assertRegex(
        output,
        rf'Session ID:[ \t]+{re.escape(session_id or "unavailable")}',
    )
    if show_resume_hint:
        assert session_id is not None
        testcase.assertRegex(
            output,
            rf'To resume this session:[ \t]+agent-resume {re.escape(session_id)}',
        )
        return
    testcase.assertNotIn('To resume this session:', output)


def _assert_slash_panel_rendered(
    testcase: unittest.TestCase,
    output: str,
    *,
    title: str,
    expected_body_line: str,
) -> None:
    testcase.assertIn(title, output)
    testcase.assertIn(expected_body_line, output)
    testcase.assertNotIn('==============', output)


class _ChatFakeAgent:
    last_client = None
    last_runtime = None
    last_session_store = None
    run_prompts: list[str] = []
    resume_calls: list[tuple[str, str | None]] = []
    queued_results: list[AgentRunResult] = []

    @classmethod
    def reset(cls) -> None:
        cls.last_client = None
        cls.last_runtime = None
        cls.last_session_store = None
        cls.run_prompts = []
        cls.resume_calls = []
        cls.queued_results = []

    @classmethod
    def queue_results(cls, *results: AgentRunResult) -> None:
        cls.queued_results = list(results)

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
        _ChatFakeAgent.last_client = client
        _ChatFakeAgent.last_runtime = SimpleNamespace(
            workspace_scope=workspace_scope,
            execution_policy=execution_policy,
            context_policy=context_policy,
            permissions=permissions,
            budget_config=budget_config,
            session_paths=session_paths,
            max_turns=execution_policy.max_turns,
        )
        _ChatFakeAgent.last_session_store = session_store
        self.mcp_runtime = SimpleNamespace(servers=('mcp-server-1',), load_errors=())
        self.workspace_gateway = SimpleNamespace(
            plugin_count=2,
            policy_count=1,
            search_provider_count=1,
            load_error_count=0,
        )

    def _emit_progress_events(self, result: AgentRunResult) -> None:
        reporter = getattr(self, 'progress_reporter', None)
        if reporter is None:
            return
        for event in result.events:
            reporter(dict(event))

    def run(self, prompt: str) -> AgentRunResult:
        _ChatFakeAgent.run_prompts.append(prompt)
        if _ChatFakeAgent.queued_results:
            result = _ChatFakeAgent.queued_results.pop(0)
            self._emit_progress_events(result)
            return result
        result = AgentRunResult(
            final_output=f'run:{prompt}',
            turns=1,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
            session_id='chat-session-001',
            session_path=str((Path.cwd() / '.port_sessions' / 'agent' / 'chat-session-001.json').resolve()),
        )
        self._emit_progress_events(result)
        return result

    def resume(self, prompt: str, session_snapshot) -> AgentRunResult:
        _ChatFakeAgent.resume_calls.append((prompt, session_snapshot.session_id))
        if _ChatFakeAgent.queued_results:
            result = _ChatFakeAgent.queued_results.pop(0)
            self._emit_progress_events(result)
            return result
        result = AgentRunResult(
            final_output=f'resumed:{prompt}',
            turns=2,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
            session_id=session_snapshot.session_id,
            session_path=str((Path.cwd() / '.port_sessions' / 'agent' / f'{session_snapshot.session_id}.json').resolve()),
        )
        self._emit_progress_events(result)
        return result


def _make_session_manager_cls(load_impl):
    class _FakeSessionManager:
        def __init__(self, directory=None) -> None:
            self.directory = directory

        def load_session(self, session_id: str) -> AgentSessionSnapshot:
            return load_impl(session_id, self.directory)

    return _FakeSessionManager


class MainChatEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        _ChatFakeAgent.reset()

    def _make_session_snapshot(self, session_id: str) -> AgentSessionSnapshot:
        return AgentSessionSnapshot(
            session_id=session_id,
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

    def test_agent_chat_runs_initial_prompt_then_exits(self) -> None:
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['第一轮', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, ['第一轮'])
        self.assertEqual(_ChatFakeAgent.resume_calls, [])
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id='chat-session-001', show_resume_hint=True)
        self.assertIn('run:第一轮', stdout.getvalue())
        self.assertNotIn('[session] chat-session-001', stdout.getvalue())

    def test_agent_chat_resumes_existing_session_from_loop(self) -> None:
        stored = self._make_session_snapshot('resume-test-001')
        load_calls: list[str] = []

        def _load_session(session_id, directory=None):
            load_calls.append(session_id)
            return stored

        with (
            patch('main.SessionManager', _make_session_manager_cls(_load_session)),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['继续', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat', '--session-id', 'resume-test-001'])

        self.assertEqual(code, 0)
        self.assertEqual(load_calls, ['resume-test-001'])
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        self.assertEqual(_ChatFakeAgent.resume_calls, [('继续', 'resume-test-001')])
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id='resume-test-001', show_resume_hint=True)
        self.assertIn('resumed:继续', stdout.getvalue())

    def test_agent_chat_quit_command_exits_without_agent_call(self) -> None:
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['/quit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        self.assertEqual(_ChatFakeAgent.resume_calls, [])
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id=None, show_resume_hint=False)

    def test_agent_chat_handles_eof(self) -> None:
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=EOFError),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        self.assertEqual(_ChatFakeAgent.resume_calls, [])
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id=None, show_resume_hint=False)

    def test_agent_chat_handles_keyboard_interrupt(self) -> None:
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=KeyboardInterrupt),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        self.assertEqual(_ChatFakeAgent.resume_calls, [])
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id=None, show_resume_hint=False)

    def test_agent_chat_clear_updates_current_session_id(self) -> None:
        load_calls: list[str] = []

        def _load_session(session_id, directory=None):
            load_calls.append(session_id)
            return self._make_session_snapshot(session_id)

        _ChatFakeAgent.queue_results(
            AgentRunResult(
                final_output='Cleared in-memory session context.\nPrevious session id: old-session\nCleared session id: cleared-002',
                turns=0,
                tool_calls=0,
                transcript=(),
                usage=TokenUsage(),
                stop_reason='slash_command',
                session_id='cleared-002',
                session_path=str((Path.cwd() / '.port_sessions' / 'agent' / 'cleared-002.json').resolve()),
            ),
            AgentRunResult(
                final_output='resumed:继续处理',
                turns=1,
                tool_calls=0,
                transcript=(),
                usage=TokenUsage(),
                session_id='cleared-002',
                session_path=str((Path.cwd() / '.port_sessions' / 'agent' / 'cleared-002.json').resolve()),
            ),
        )

        with (
            patch('main.SessionManager', _make_session_manager_cls(_load_session)),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['/clear', '继续处理', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat', '--session-id', 'old-session'])

        self.assertEqual(code, 0)
        self.assertEqual(load_calls, ['old-session', 'cleared-002'])
        self.assertEqual(_ChatFakeAgent.resume_calls, [('/clear', 'old-session'), ('继续处理', 'cleared-002')])
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id='cleared-002', show_resume_hint=True)
        self.assertIn('Cleared session id: cleared-002', stdout.getvalue())

    def test_agent_chat_renders_slash_status_as_panel(self) -> None:
        _ChatFakeAgent.queue_results(
            AgentRunResult(
                final_output=(
                    'Session Status\n'
                    '==============\n'
                    'Session id: chat-session-001\n'
                    'Model: demo-model\n'
                    f'Working directory: {Path.cwd()}\n'
                    'Completed turns: 0\n'
                    'Tool calls: 0'
                ),
                turns=0,
                tool_calls=0,
                transcript=(),
                usage=TokenUsage(),
                stop_reason='slash_command',
                session_id='chat-session-001',
                session_path=str((Path.cwd() / '.port_sessions' / 'agent' / 'chat-session-001.json').resolve()),
                events=(
                    {'type': 'slash_command', 'command': 'status', 'mode': 'read_only'},
                ),
            )
        )

        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['/status', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_slash_panel_rendered(
            self,
            stdout.getvalue(),
            title='Session Status',
            expected_body_line='Working directory:',
        )
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id='chat-session-001', show_resume_hint=True)

    def test_agent_chat_prints_progress_events_by_default(self) -> None:
        _ChatFakeAgent.queue_results(
            AgentRunResult(
                final_output='已完成',
                turns=1,
                tool_calls=1,
                transcript=(),
                events=(
                    {'type': 'model_turn', 'turn': 1, 'finish_reason': 'tool_calls', 'tool_calls': 1},
                    {
                        'type': 'tool_result',
                        'turn': 1,
                        'tool_call_id': 'call_001',
                        'tool_name': 'bash',
                        'ok': True,
                        'metadata': {'action': 'bash'},
                    },
                ),
                usage=TokenUsage(),
                session_id='chat-session-001',
                session_path=str((Path.cwd() / '.port_sessions' / 'agent' / 'chat-session-001.json').resolve()),
            )
        )

        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['显示进度', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn('[progress] turn 1 model finished: finish_reason=tool_calls tool_calls=1', output)
        self.assertIn('[progress] turn 1 tool bash finished: ok=True', output)
        self.assertLess(
            output.index('[progress] turn 1 model finished: finish_reason=tool_calls tool_calls=1'),
            output.index('已完成'),
        )

    def test_agent_chat_can_disable_progress_output(self) -> None:
        _ChatFakeAgent.queue_results(
            AgentRunResult(
                final_output='静默完成',
                turns=1,
                tool_calls=1,
                transcript=(),
                events=(
                    {'type': 'model_turn', 'turn': 1, 'finish_reason': 'tool_calls', 'tool_calls': 1},
                    {
                        'type': 'tool_result',
                        'turn': 1,
                        'tool_call_id': 'call_002',
                        'tool_name': 'bash',
                        'ok': True,
                        'metadata': {'action': 'bash'},
                    },
                ),
                usage=TokenUsage(),
                session_id='chat-session-001',
                session_path=str((Path.cwd() / '.port_sessions' / 'agent' / 'chat-session-001.json').resolve()),
            )
        )

        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['关闭进度', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat', '--no-show-progress'])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn('静默完成', output)
        self.assertNotIn('[progress]', output)

    def test_agent_chat_surfaces_backend_error_when_final_output_is_empty(self) -> None:
        _ChatFakeAgent.queue_results(
            AgentRunResult(
                final_output='',
                turns=1,
                tool_calls=0,
                transcript=(),
                events=(
                    {
                        'type': 'backend_error',
                        'turn': 1,
                        'error': 'HTTP 403 from model backend: quota exhausted',
                    },
                ),
                usage=TokenUsage(),
                stop_reason='backend_error',
                session_id='chat-session-001',
                session_path=str((Path.cwd() / '.port_sessions' / 'agent' / 'chat-session-001.json').resolve()),
            )
        )

        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['失败请求', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn('[error] HTTP 403 from model backend: quota exhausted', output)
        self.assertNotIn('[session] chat-session-001', output)

    # ------------------------------------------------------------------
    # agent / agent-resume 与 agent-chat 共用同一交互循环
    # ------------------------------------------------------------------

    def test_agent_enters_interactive_loop(self) -> None:
        """agent 命令应直接进入交互循环；第一轮走 run，后续轮次自动切换为 resume。"""
        stored_snapshot = self._make_session_snapshot('chat-session-001')

        def _load_session(session_id, directory=None):
            return stored_snapshot

        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.SessionManager', _make_session_manager_cls(_load_session)),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['第一轮', '第二轮', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, ['第一轮'])
        self.assertEqual(_ChatFakeAgent.resume_calls, [('第二轮', 'chat-session-001')])
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id='chat-session-001', show_resume_hint=True)
        self.assertIn('run:第一轮', stdout.getvalue())
        self.assertIn('resumed:第二轮', stdout.getvalue())

    def test_agent_enters_interactive_loop_eof(self) -> None:
        """agent 命令在 EOF 时应正常退出，返回码为 0。"""
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=EOFError),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id=None, show_resume_hint=False)
        

    def test_agent_resume_enters_interactive_loop(self) -> None:
        """agent-resume 命令应加载存档会话并进入多轮交互循环。"""
        stored = self._make_session_snapshot('resume-loop-001')
        load_calls: list[str] = []

        def _load_session(session_id, directory=None):
            load_calls.append(session_id)
            return stored

        with (
            patch('main.SessionManager', _make_session_manager_cls(_load_session)),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['第一轮续跑', '第二轮续跑', '/quit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-resume', 'resume-loop-001'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        self.assertEqual(
            _ChatFakeAgent.resume_calls,
            [('第一轮续跑', 'resume-loop-001'), ('第二轮续跑', 'resume-loop-001')],
        )
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id='resume-loop-001', show_resume_hint=True)
        self.assertIn('resumed:第一轮续跑', stdout.getvalue())
        self.assertIn('resumed:第二轮续跑', stdout.getvalue())

    def test_agent_resume_skips_empty_input(self) -> None:
        """agent-resume 命令在空输入时应跳过，不触发 agent.resume。"""
        stored = self._make_session_snapshot('resume-empty-001')

        def _load_session(session_id, directory=None):
            return stored

        with (
            patch('main.SessionManager', _make_session_manager_cls(_load_session)),
            patch('main.Agent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['', '   ', '有效输入', '/exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-resume', 'resume-empty-001'])

        self.assertEqual(code, 0)
        self.assertEqual(len(_ChatFakeAgent.resume_calls), 1)
        self.assertEqual(_ChatFakeAgent.resume_calls[0][0], '有效输入')
        _assert_banner_rendered(self, stdout.getvalue())
        _assert_exit_summary_rendered(self, stdout.getvalue(), session_id='resume-empty-001', show_resume_hint=True)


if __name__ == '__main__':
    unittest.main()
