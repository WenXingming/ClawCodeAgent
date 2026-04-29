"""ISSUE-012 slash 命令模块单元测试。"""

from __future__ import annotations

from dataclasses import replace
import unittest
from pathlib import Path

from core_contracts.budget import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.runtime_policy import ContextPolicy, WorkspaceScope
from interaction.slash_commands import (
    SlashCommandContext,
    SlashCommandDispatcher,
)
from session.session_state import AgentSessionState
from tools.tools_gateway import ToolsGateway


class SlashCommandModuleTests(unittest.TestCase):
    """验证 slash 解析与本地命令分发。"""

    def setUp(self) -> None:
        """为每个测试用例创建独立的 slash 分发器实例。"""
        self.dispatcher = SlashCommandDispatcher()
        self.tool_gateway = ToolsGateway()

    def _make_context(self) -> SlashCommandContext:
        session_state = AgentSessionState()
        session_state.append_user('历史问题')
        session_state.transcript_entries.append({'role': 'assistant', 'content': '历史回答'})
        return SlashCommandContext(
            session_state=session_state,
            session_id='session-001',
            turns_offset=2,
            tool_call_count=3,
            workspace_scope=WorkspaceScope(cwd=Path('.').resolve()),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(
                allow_file_write=True,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            ),
            budget_config=BudgetConfig(),
            model_config=ModelConfig(model='demo-model'),
            tool_registry=self.tool_gateway.default_registry(),
        )

    def test_parse_slash_command_extracts_name_and_arguments(self) -> None:
        parsed = self.dispatcher.parse_slash_command('/help extra words')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.command_name, 'help')
        self.assertEqual(parsed.arguments, 'extra words')

    def test_parse_slash_command_returns_none_for_regular_prompt(self) -> None:
        self.assertIsNone(self.dispatcher.parse_slash_command('hello world'))

    def test_dispatcher_public_api_supports_parse_and_lookup(self) -> None:
        parsed = self.dispatcher.parse_slash_command('/tools verbose')
        spec = self.dispatcher.find_slash_command('TOOLS')

        self.assertIsNotNone(parsed)
        self.assertIsNotNone(spec)
        assert parsed is not None
        assert spec is not None
        self.assertEqual(parsed.command_name, 'tools')
        self.assertEqual(spec.names[0], 'tools')

    def test_get_command_completions_returns_matching_names_and_aliases(self) -> None:
        self.assertEqual(self.dispatcher.get_command_completions('st'), ('status',))
        self.assertEqual(self.dispatcher.get_command_completions('q'), ('quit',))

    def test_dispatch_unknown_command_returns_local_error(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/unknown')
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertEqual(result.metadata.get('error'), 'unknown_command')
        self.assertIn('Unknown slash command', result.output)

    def test_dispatch_unique_prefix_resolves_to_matching_command(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/st')

        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertEqual(result.command_name, 'status')
        self.assertEqual(result.metadata.get('match_mode'), 'prefix')
        self.assertEqual(result.metadata.get('typed_name'), 'st')
        self.assertEqual(result.metadata.get('matched_name'), 'status')
        self.assertIn('Session id: session-001', result.output)

    def test_dispatch_ambiguous_prefix_returns_candidates(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/c')

        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertEqual(result.metadata.get('error'), 'ambiguous_command')
        self.assertIn('/context - Show local context status.', result.output)
        self.assertIn('/clear - Fork a new cleared session snapshot.', result.output)

    def test_dispatch_bare_slash_lists_supported_commands(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/')

        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertEqual(result.command_name, 'help')
        self.assertEqual(result.metadata.get('match_mode'), 'list_all')
        self.assertIn('/help - Show supported local slash commands.', result.output)
        self.assertIn('Tip: input / to list commands', result.output)

    def test_dispatch_context_uses_current_session_only(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/context')
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertIn('Messages: 1', result.output)
        self.assertIn('Transcript entries: 2', result.output)

    def test_dispatch_tools_lists_registered_tools(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/tools')
        self.assertTrue(result.handled)
        self.assertIn('read_file -', result.output)
        self.assertIn('Shell enabled: no', result.output)

    def test_dispatch_tools_renders_plugin_summary_when_present(self) -> None:
        context = replace(
            self._make_context(),
            plugin_summary='Discovered Plugins\n==================\ndemo-plugin - plugin summary',
        )

        result = self.dispatcher.dispatch_slash_command(context, '/tools')

        self.assertTrue(result.handled)
        self.assertIn('Discovered Plugins', result.output)
        self.assertIn('demo-plugin - plugin summary', result.output)

    def test_dispatch_clear_requests_forked_empty_session(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/clear')
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertTrue(result.fork_session)
        self.assertIsNotNone(result.replacement_session_state)
        self.assertEqual(result.replacement_session_state.messages, [])
        self.assertEqual(result.replacement_session_state.transcript_entries, [])
        self.assertTrue(result.metadata.get('had_history'))

    def test_dispatch_exit_stops_query_path(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/exit')
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertEqual(result.command_name, 'exit')
        self.assertEqual(result.output, 'Exiting local session interaction.')
        self.assertTrue(result.metadata.get('exit_requested'))

    def test_dispatch_quit_alias_maps_to_exit_handler(self) -> None:
        result = self.dispatcher.dispatch_slash_command(self._make_context(), '/quit')
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)
        self.assertEqual(result.command_name, 'quit')
        self.assertEqual(result.output, 'Exiting local session interaction.')
        self.assertTrue(result.metadata.get('exit_requested'))


if __name__ == '__main__':
    unittest.main()