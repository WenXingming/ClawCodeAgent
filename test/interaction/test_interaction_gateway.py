"""InteractionGateway 门面单元测试。

通过 create_interaction_gateway 工厂和 MagicMock 隔离全部子组件依赖，
只验证网关自身的编排逻辑与契约翻译是否正确。
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock

from context import create_context_gateway
from core_contracts.interaction_contracts import (
    EnvironmentLoadSummary,
    ParsedSlashCommand,
    SessionSummary,
    SlashCommandContext,
    SlashCommandResolution,
    SlashCommandResult,
    SlashCommandSpec,
)
from core_contracts.outcomes import AgentRunResult
from core_contracts.primitives import TokenUsage
from interaction import (
    InteractionGateway,
    create_interaction_gateway,
    SlashCommandRenderer,
    SlashAutocompletePrompt,
    SlashAutocompleteEntry,
)


def _make_agent_run_result(**overrides: object) -> AgentRunResult:
    """构造最简 AgentRunResult。"""
    defaults: dict[str, object] = {
        'final_output': 'done',
        'turns': 1,
        'tool_calls': 2,
        'transcript': (),
        'events': (
            {'type': 'tool_result', 'ok': True},
            {'type': 'tool_result', 'ok': False},
        ),
        'usage': TokenUsage(),
        'session_id': 'test-session',
    }
    defaults.update(overrides)
    return AgentRunResult(**defaults)  # type: ignore[arg-type]


class GatewayConstructionTests(unittest.TestCase):
    """InteractionGateway 构造与工厂测试。"""

    def test_factory_creates_gateway_without_context_gateway(self) -> None:
        gw = create_interaction_gateway(
            stream=io.StringIO(),
            stdin=io.StringIO(),
            startup_lines=('TITLE',),
            startup_subtitle='SUB',
        )
        self.assertIsNotNone(gw)

    def test_factory_creates_gateway_with_context_gateway(self) -> None:
        ctx_gw = create_context_gateway()
        gw = create_interaction_gateway(context_gateway=ctx_gw)
        self.assertIsNotNone(gw)

    def test_factory_default_stream_and_stdin(self) -> None:
        """未提供 stream/stdin 时使用 sys.stdout/sys.stdin。"""
        gw = create_interaction_gateway()
        self.assertIsNotNone(gw)


class GatewayRenderingTests(unittest.TestCase):
    """render_startup / render_exit / render_slash_result 委托验证。"""

    def setUp(self) -> None:
        self.stream = io.StringIO()
        self.gw = create_interaction_gateway(stream=self.stream)

    def test_render_startup_writes_to_stream(self) -> None:
        env_summary = EnvironmentLoadSummary(mcp_servers=2, plugins=1)
        self.gw.render_startup(environment_summary=env_summary)
        output = self.stream.getvalue()
        self.assertIn('Tudou Code Agent', output)
        self.assertIn('Environment loaded:', output)

    def test_render_startup_without_environment_summary(self) -> None:
        self.gw.render_startup()
        output = self.stream.getvalue()
        self.assertIn('Tudou Code Agent', output)
        self.assertNotIn('Environment loaded:', output)

    def test_render_startup_uses_explicit_stream(self) -> None:
        explicit = io.StringIO()
        self.gw.render_startup(stream=explicit)
        self.assertNotEqual(explicit.getvalue(), '')
        self.assertEqual(self.stream.getvalue(), '')

    def test_render_exit_writes_summary_box(self) -> None:
        summary = SessionSummary(
            session_id='s-1',
            tool_calls=5,
            tool_successes=4,
            tool_failures=1,
            wall_time_seconds=60,
        )
        self.gw.render_exit(summary)
        output = self.stream.getvalue()
        self.assertIn('Interaction Summary', output)
        self.assertIn('s-1', output)
        self.assertIn('Tool Calls', output)

    def test_render_slash_result_writes_panel(self) -> None:
        self.gw.render_slash_result(
            command_name='status',
            output='Session: active',
            metadata={'mode': 'test'},
        )
        output = self.stream.getvalue()
        self.assertIn('Session Status', output)
        self.assertIn('Session: active', output)


class GatewaySlashDelegationTests(unittest.TestCase):
    """slash 命令相关 API 的委托测试。"""

    def setUp(self) -> None:
        self.gw = create_interaction_gateway()

    def test_dispatch_slash_command_returns_result(self) -> None:
        from core_contracts.config import (
            BudgetConfig,
            ContextPolicy,
            WorkspaceScope,
        )
        from core_contracts.model import ModelConfig
        from core_contracts.session_contracts import AgentSessionState
        from core_contracts.tools_contracts import ToolPermissionPolicy, ToolRegistry

        session_state = AgentSessionState()
        session_state.append_user('hello')
        context = SlashCommandContext(
            session_state=session_state,
            session_id='s-1',
            turns_offset=0,
            tool_call_count=0,
            workspace_scope=WorkspaceScope(cwd='.'),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            budget_config=BudgetConfig(),
            model_config=ModelConfig(model='test'),
            tool_registry=ToolRegistry.from_tools(),
        )

        result = self.gw.dispatch_slash_command(context, '/help')
        self.assertIsInstance(result, SlashCommandResult)
        self.assertTrue(result.handled)
        self.assertFalse(result.continue_query)

    def test_dispatch_regular_prompt_passes_through(self) -> None:
        from core_contracts.config import (
            BudgetConfig,
            ContextPolicy,
            WorkspaceScope,
        )
        from core_contracts.model import ModelConfig
        from core_contracts.session_contracts import AgentSessionState
        from core_contracts.tools_contracts import ToolPermissionPolicy, ToolRegistry

        session_state = AgentSessionState()
        context = SlashCommandContext(
            session_state=session_state,
            session_id='s-1',
            turns_offset=0,
            tool_call_count=0,
            workspace_scope=WorkspaceScope(cwd='.'),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            budget_config=BudgetConfig(),
            model_config=ModelConfig(model='test'),
            tool_registry=ToolRegistry.from_tools(),
        )

        result = self.gw.dispatch_slash_command(context, 'regular chat prompt')
        self.assertIsInstance(result, SlashCommandResult)
        self.assertFalse(result.handled)
        self.assertTrue(result.continue_query)
        self.assertEqual(result.prompt, 'regular chat prompt')

    def test_parse_slash_command_extracts_name_and_args(self) -> None:
        parsed = self.gw.parse_slash_command('/help verbose')
        self.assertIsInstance(parsed, ParsedSlashCommand)
        self.assertEqual(parsed.command_name, 'help')
        self.assertEqual(parsed.arguments, 'verbose')

    def test_parse_slash_command_returns_none_for_regular_text(self) -> None:
        parsed = self.gw.parse_slash_command('hello world')
        self.assertIsNone(parsed)

    def test_find_slash_command_returns_spec(self) -> None:
        spec = self.gw.find_slash_command('help')
        self.assertIsInstance(spec, SlashCommandSpec)

    def test_find_slash_command_returns_none_for_unknown(self) -> None:
        spec = self.gw.find_slash_command('nonexistent_command_xyz')
        self.assertIsNone(spec)

    def test_resolve_slash_command_exact_match(self) -> None:
        resolution = self.gw.resolve_slash_command('help')
        self.assertIsInstance(resolution, SlashCommandResolution)
        self.assertEqual(resolution.kind, 'exact')

    def test_resolve_slash_command_unique_prefix(self) -> None:
        resolution = self.gw.resolve_slash_command('stat')
        self.assertIsInstance(resolution, SlashCommandResolution)
        self.assertEqual(resolution.kind, 'prefix')
        self.assertEqual(resolution.matched_name, 'status')

    def test_resolve_slash_command_ambiguous(self) -> None:
        resolution = self.gw.resolve_slash_command('c')
        self.assertEqual(resolution.kind, 'ambiguous')

    def test_resolve_slash_command_unknown(self) -> None:
        resolution = self.gw.resolve_slash_command('xyz')
        self.assertEqual(resolution.kind, 'none')

    def test_get_slash_command_specs_returns_non_empty_tuple(self) -> None:
        specs = self.gw.get_slash_command_specs()
        self.assertIsInstance(specs, tuple)
        self.assertGreater(len(specs), 0)

    def test_get_autocomplete_entries_expands_aliases(self) -> None:
        entries = self.gw.get_autocomplete_entries()
        names = {e.name for e in entries}
        self.assertIn('exit', names)
        self.assertIn('quit', names)


class GatewaySessionTrackerTests(unittest.TestCase):
    """start_session_tracker / observe_run_result / get_session_summary 测试。"""

    def setUp(self) -> None:
        self.gw = create_interaction_gateway()

    def test_get_session_summary_returns_default_when_tracker_not_started(self) -> None:
        summary = self.gw.get_session_summary()
        self.assertIsInstance(summary, SessionSummary)
        self.assertEqual(summary.tool_calls, 0)

    def test_tracker_lifecycle_produces_correct_summary(self) -> None:
        self.gw.start_session_tracker(session_id='lifecycle-test')
        result = _make_agent_run_result(session_id='lifecycle-test')
        self.gw.observe_run_result(result, current_session_id='lifecycle-test')

        summary = self.gw.get_session_summary()
        self.assertEqual(summary.session_id, 'lifecycle-test')
        self.assertEqual(summary.tool_calls, 2)
        self.assertEqual(summary.tool_successes, 1)
        self.assertEqual(summary.tool_failures, 1)

    def test_observe_run_result_is_noop_when_tracker_not_started(self) -> None:
        """未调用 start_session_tracker 时 observe_run_result 不应崩溃。"""
        result = _make_agent_run_result()
        self.gw.observe_run_result(result, current_session_id='any')
        summary = self.gw.get_session_summary()
        self.assertEqual(summary.tool_calls, 0)

    def test_restart_tracker_resets_counters(self) -> None:
        self.gw.start_session_tracker(session_id='first')
        self.gw.observe_run_result(_make_agent_run_result(), current_session_id='first')
        first_summary = self.gw.get_session_summary()
        self.assertEqual(first_summary.tool_calls, 2)

        self.gw.start_session_tracker(session_id='second')
        second_summary = self.gw.get_session_summary()
        self.assertEqual(second_summary.tool_calls, 0)

    def test_observe_run_result_updates_session_id_from_result(self) -> None:
        self.gw.start_session_tracker()
        result = _make_agent_run_result(session_id='from-result')
        self.gw.observe_run_result(result, current_session_id='fallback')
        summary = self.gw.get_session_summary()
        self.assertEqual(summary.session_id, 'from-result')

    def test_observe_run_result_falls_back_to_current_id(self) -> None:
        self.gw.start_session_tracker()
        result = _make_agent_run_result(session_id=None)
        self.gw.observe_run_result(result, current_session_id='fallback-id')
        summary = self.gw.get_session_summary()
        self.assertEqual(summary.session_id, 'fallback-id')


class GatewayBuildProgressReporterTests(unittest.TestCase):
    """build_progress_reporter / flush_runtime_events 测试。"""

    def setUp(self) -> None:
        self.gw = create_interaction_gateway(stream=io.StringIO())

    def test_build_progress_reporter_returns_callable(self) -> None:
        reporter = self.gw.build_progress_reporter()
        self.assertTrue(callable(reporter))

    def test_progress_reporter_accepts_events(self) -> None:
        reporter = self.gw.build_progress_reporter()
        reporter({'type': 'model_start', 'turn': 0})
        reporter({'type': 'model_turn', 'turn': 0, 'finish_reason': 'stop', 'tool_calls': 1})
        self.gw.flush_runtime_events()

    def test_flush_does_not_raise_on_no_prior_events(self) -> None:
        self.gw.flush_runtime_events()


if __name__ == '__main__':
    unittest.main()
