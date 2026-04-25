"""ISSUE-012 slash 命令模块单元测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from control_plane.slash_commands import (
    SlashCommandContext,
    dispatch_slash_command,
    parse_slash_command,
)
from core_contracts.config import AgentPermissions, AgentRuntimeConfig, ModelConfig
from session.session_state import AgentSessionState
from tools.agent_tools import default_tool_registry


class SlashCommandModuleTests(unittest.TestCase):
    """验证 slash 解析与本地命令分发。"""

    def _make_context(self) -> SlashCommandContext:
        session_state = AgentSessionState()
        session_state.append_user('历史问题')
        session_state.transcript_entries.append({'role': 'assistant', 'content': '历史回答'})
        return SlashCommandContext(
            session_state=session_state,
            session_id='session-001',
            turns_offset=2,
            runtime_config=AgentRuntimeConfig(
                cwd=Path('.').resolve(),
                permissions=AgentPermissions(
                    allow_file_write=True,
                    allow_shell_commands=False,
                    allow_destructive_shell_commands=False,
                ),
            ),
            model_config=ModelConfig(model='demo-model'),
            tool_registry=default_tool_registry(),
        )

    def test_parse_slash_command_extracts_name_and_arguments(self) -> None:
        parsed = parse_slash_command('/help extra words')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.command_name, 'help')
        self.assertEqual(parsed.arguments, 'extra words')

    def test_parse_slash_command_returns_none_for_regular_prompt(self) -> None:
        self.assertIsNone(parse_slash_command('hello world'))

    def test_dispatch_unknown_command_returns_local_error(self) -> None:
        result = dispatch_slash_command(self._make_context(), '/unknown')
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertEqual(result.metadata.get('error'), 'unknown_command')
        self.assertIn('Unknown slash command', result.output)

    def test_dispatch_context_uses_current_session_only(self) -> None:
        result = dispatch_slash_command(self._make_context(), '/context')
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertIn('Messages: 1', result.output)
        self.assertIn('Transcript entries: 2', result.output)

    def test_dispatch_tools_lists_registered_tools(self) -> None:
        result = dispatch_slash_command(self._make_context(), '/tools')
        self.assertTrue(result.handled)
        self.assertIn('read_file -', result.output)
        self.assertIn('Shell enabled: no', result.output)

    def test_dispatch_clear_requests_forked_empty_session(self) -> None:
        result = dispatch_slash_command(self._make_context(), '/clear')
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertTrue(result.fork_session)
        self.assertIsNotNone(result.replacement_session_state)
        self.assertEqual(result.replacement_session_state.messages, [])
        self.assertEqual(result.replacement_session_state.transcript_entries, [])
        self.assertTrue(result.metadata.get('had_history'))


if __name__ == '__main__':
    unittest.main()