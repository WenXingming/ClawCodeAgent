"""ISSUE-013 交互式 chat CLI 测试。"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from core_contracts.config import AgentRuntimeConfig, ModelConfig
from core_contracts.result import AgentRunResult
from core_contracts.usage import TokenUsage
from main import main
from session.session_snapshot import AgentSessionSnapshot


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

    def __init__(self, client, runtime_config, session_store) -> None:
        _ChatFakeAgent.last_client = client
        _ChatFakeAgent.last_runtime = runtime_config
        _ChatFakeAgent.last_session_store = session_store

    def run(self, prompt: str) -> AgentRunResult:
        _ChatFakeAgent.run_prompts.append(prompt)
        if _ChatFakeAgent.queued_results:
            return _ChatFakeAgent.queued_results.pop(0)
        return AgentRunResult(
            final_output=f'run:{prompt}',
            turns=1,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
            session_id='chat-session-001',
            session_path=str((Path.cwd() / '.port_sessions' / 'agent' / 'chat-session-001.json').resolve()),
        )

    def resume(self, prompt: str, session_snapshot) -> AgentRunResult:
        _ChatFakeAgent.resume_calls.append((prompt, session_snapshot.session_id))
        if _ChatFakeAgent.queued_results:
            return _ChatFakeAgent.queued_results.pop(0)
        return AgentRunResult(
            final_output=f'resumed:{prompt}',
            turns=2,
            tool_calls=0,
            transcript=(),
            usage=TokenUsage(),
            session_id=session_snapshot.session_id,
            session_path=str((Path.cwd() / '.port_sessions' / 'agent' / f'{session_snapshot.session_id}.json').resolve()),
        )


def _make_session_store_cls(load_impl):
    class _FakeSessionStore:
        def __init__(self, directory=None) -> None:
            self.directory = directory

        def load(self, session_id: str) -> AgentSessionSnapshot:
            return load_impl(session_id, self.directory)

    return _FakeSessionStore


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
            runtime_config=AgentRuntimeConfig(cwd=Path('.').resolve()),
            messages=({'role': 'user', 'content': '历史问题'},),
        )

    def test_agent_chat_runs_initial_prompt_then_exits(self) -> None:
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['第一轮', '.exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, ['第一轮'])
        self.assertEqual(_ChatFakeAgent.resume_calls, [])
        self.assertIn('run:第一轮', stdout.getvalue())
        self.assertIn('[session] chat-session-001', stdout.getvalue())

    def test_agent_chat_resumes_existing_session_from_loop(self) -> None:
        stored = self._make_session_snapshot('resume-test-001')
        load_calls: list[str] = []

        def _load_session(session_id, directory=None):
            load_calls.append(session_id)
            return stored

        with (
            patch('main.AgentSessionStore', _make_session_store_cls(_load_session)),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['继续', '.exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat', '--session-id', 'resume-test-001'])

        self.assertEqual(code, 0)
        self.assertEqual(load_calls, ['resume-test-001'])
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        self.assertEqual(_ChatFakeAgent.resume_calls, [('继续', 'resume-test-001')])
        self.assertIn('resumed:继续', stdout.getvalue())

    def test_agent_chat_quit_command_exits_without_agent_call(self) -> None:
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['.quit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        self.assertEqual(_ChatFakeAgent.resume_calls, [])

    def test_agent_chat_handles_eof(self) -> None:
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=EOFError),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, [])
        self.assertEqual(_ChatFakeAgent.resume_calls, [])

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
            patch('main.AgentSessionStore', _make_session_store_cls(_load_session)),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['/clear', '继续处理', '.exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-chat', '--session-id', 'old-session'])

        self.assertEqual(code, 0)
        self.assertEqual(load_calls, ['old-session', 'cleared-002'])
        self.assertEqual(_ChatFakeAgent.resume_calls, [('/clear', 'old-session'), ('继续处理', 'cleared-002')])
        self.assertIn('Cleared session id: cleared-002', stdout.getvalue())

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
            patch('main.AgentSessionStore', _make_session_store_cls(_load_session)),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['第一轮', '第二轮', '.exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, ['第一轮'])
        self.assertEqual(_ChatFakeAgent.resume_calls, [('第二轮', 'chat-session-001')])
        self.assertIn('run:第一轮', stdout.getvalue())
        self.assertIn('resumed:第二轮', stdout.getvalue())

    def test_agent_enters_interactive_loop_eof(self) -> None:
        """agent 命令在 EOF 时应正常退出，返回码为 0。"""
        with (
            patch.dict(os.environ, {'OPENAI_MODEL': 'demo-model', 'OPENAI_API_KEY': 'demo-key'}, clear=False),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=EOFError),
        ):
            code = main(['agent'])

        self.assertEqual(code, 0)
        self.assertEqual(_ChatFakeAgent.run_prompts, [])

    def test_agent_resume_enters_interactive_loop(self) -> None:
        """agent-resume 命令应加载存档会话并进入多轮交互循环。"""
        stored = self._make_session_snapshot('resume-loop-001')
        load_calls: list[str] = []

        def _load_session(session_id, directory=None):
            load_calls.append(session_id)
            return stored

        with (
            patch('main.AgentSessionStore', _make_session_store_cls(_load_session)),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['第一轮续跑', '第二轮续跑', '.quit']),
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
        self.assertIn('resumed:第一轮续跑', stdout.getvalue())
        self.assertIn('resumed:第二轮续跑', stdout.getvalue())

    def test_agent_resume_skips_empty_input(self) -> None:
        """agent-resume 命令在空输入时应跳过，不触发 agent.resume。"""
        stored = self._make_session_snapshot('resume-empty-001')

        def _load_session(session_id, directory=None):
            return stored

        with (
            patch('main.AgentSessionStore', _make_session_store_cls(_load_session)),
            patch('main.LocalCodingAgent', _ChatFakeAgent),
            patch('builtins.input', side_effect=['', '   ', '有效输入', '.exit']),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(['agent-resume', 'resume-empty-001'])

        self.assertEqual(code, 0)
        self.assertEqual(len(_ChatFakeAgent.resume_calls), 1)
        self.assertEqual(_ChatFakeAgent.resume_calls[0][0], '有效输入')


if __name__ == '__main__':
    unittest.main()