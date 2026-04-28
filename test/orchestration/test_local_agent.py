"""LocalAgent 最小闭环测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

from core_contracts.config import AgentPermissions, AgentRuntimeConfig, BudgetConfig, ModelConfig
from core_contracts.protocol import OneTurnResponse, ToolCall
from core_contracts.token_usage import TokenUsage
from extensions.search_runtime import SearchResult, SearchResponse, SearchProviderProfile
from openai_client.openai_client import OpenAIClient, OpenAIConnectionError, OpenAIResponseError
from orchestration.local_agent import LocalAgent
from session.session_snapshot import AgentSessionSnapshot
from session.session_store import AgentSessionStore
from tools.mcp_models import MCPCapability, MCPTool, MCPToolCallResult


_TEST_TMP_ROOT = (Path(__file__).resolve().parent / '.tmp').resolve()


def _make_test_dir() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = _TEST_TMP_ROOT / f'case-{uuid4().hex}'
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


class _FakeOpenAIClient(OpenAIClient):
    """用预置响应替代真实网络调用，确保测试稳定。"""

    def __init__(self, responses: list[OneTurnResponse | Exception]) -> None:
        super().__init__(
            ModelConfig(
                model='fake-model',
                base_url='http://127.0.0.1:1/v1',
                api_key='fake-key',
                temperature=0.0,
            )
        )
        self._responses = list(responses)
        self.calls: list[list[dict]] = []  # 记录每轮入参消息，便于断言循环行为。
        self.tools_seen: list[list[dict]] = []  # 记录每轮发送给模型的工具定义。

    def complete(self, messages, tools=None, *, output_schema=None):  # type: ignore[override]
        self.calls.append([dict(item) for item in messages])
        self.tools_seen.append([dict(item) for item in (tools or []) if isinstance(item, dict)])
        if not self._responses:
            raise AssertionError('No prepared response left for test')

        current = self._responses.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class LocalAgentTests(unittest.TestCase):
    """验证 ISSUE-006 主循环最小闭环。"""

    def _build_runtime_config(self, workspace: Path, *, max_turns: int = 6) -> AgentRuntimeConfig:
        return AgentRuntimeConfig(
            cwd=workspace,
            max_turns=max_turns,
            session_directory=(workspace / 'sessions'),
            permissions=AgentPermissions(
                allow_file_write=True,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            ),
        )

    def _load_session_snapshot(self, workspace: Path, session_id: str) -> AgentSessionSnapshot:
        return AgentSessionStore(workspace / 'sessions').load(session_id)

    def _build_agent(self, fake_client: OpenAIClient, config: AgentRuntimeConfig) -> LocalAgent:
        return LocalAgent(fake_client, config, AgentSessionStore(config.session_directory))

    def test_run_without_tool_calls_returns_immediately(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='直接返回答案',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=3),
                )
            ]
        )
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('你好')
        stored = self._load_session_snapshot(workspace, result.session_id or '')
        self.assertIsNotNone(result.session_id)
        self.assertIsNotNone(result.session_path)
        self.assertTrue(Path(result.session_path or '').exists())

        self.assertEqual(result.final_output, '直接返回答案')
        self.assertEqual(result.turns, 1)
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.usage.input_tokens, 2)
        self.assertEqual(result.usage.output_tokens, 3)
        self.assertGreaterEqual(result.total_cost_usd, 0.0)
        self.assertEqual(stored.final_output, '直接返回答案')
        self.assertEqual(stored.stop_reason, 'stop')
        self.assertEqual(stored.messages[0]['content'], '你好')
        self.assertEqual(len(result.transcript), 2)  # user + assistant

    def test_run_reports_progress_events_to_reporter(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(id='call_list_001', name='list_dir', arguments={'path': '.'}),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=3, output_tokens=1),
                ),
                OneTurnResponse(
                    content='目录已列出',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ]
        )
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        progress_events: list[dict] = []
        agent.progress_reporter = lambda event: progress_events.append(dict(event))

        result = agent.run('列出当前目录')

        event_types = [item.get('type') for item in progress_events]
        self.assertIn('model_start', event_types)
        self.assertIn('model_turn', event_types)
        self.assertIn('tool_start', event_types)
        self.assertIn('tool_result', event_types)
        self.assertLess(event_types.index('model_start'), event_types.index('model_turn'))
        self.assertLess(event_types.index('tool_start'), event_types.index('tool_result'))
        self.assertFalse(any(item.get('type') == 'model_start' for item in result.events))
        self.assertTrue(any(item.get('type') == 'tool_result' for item in result.events))
        self.assertEqual(result.final_output, '目录已列出')

    def test_run_help_slash_bypasses_model_and_transcript(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        result = agent.run('/help')
        stored = self._load_session_snapshot(workspace, result.session_id or '')

        self.assertEqual(len(fake_client.calls), 0)
        self.assertEqual(result.stop_reason, 'slash_command')
        self.assertEqual(result.turns, 0)
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.transcript, ())
        self.assertEqual(stored.messages, ())
        self.assertEqual(stored.transcript, ())
        self.assertTrue(any(item.get('type') == 'slash_command' for item in result.events))
        self.assertIn('/help', result.final_output)

    def test_resume_status_slash_bypasses_model_and_preserves_history(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='历史回答',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=2, output_tokens=1),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        first = agent.run('历史问题')
        stored = self._load_session_snapshot(workspace, first.session_id or '')
        second = agent.resume('/status', stored)
        restored = self._load_session_snapshot(workspace, second.session_id or '')

        self.assertEqual(len(fake_client.calls), 1)
        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(second.stop_reason, 'slash_command')
        self.assertEqual(second.turns, 1)
        self.assertEqual(second.transcript, stored.transcript)
        self.assertEqual(restored.messages, stored.messages)
        self.assertEqual(restored.transcript, stored.transcript)
        self.assertIn('Session id:', second.final_output)
        self.assertIn(first.session_id or '', second.final_output)

    def test_run_status_prefix_slash_bypasses_model_and_resolves_to_status(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        result = agent.run('/st')
        stored = self._load_session_snapshot(workspace, result.session_id or '')

        self.assertEqual(len(fake_client.calls), 0)
        self.assertEqual(result.stop_reason, 'slash_command')
        self.assertEqual(result.turns, 0)
        self.assertEqual(stored.messages, ())
        self.assertIn('Session id:', result.final_output)
        self.assertTrue(any(item.get('match_mode') == 'prefix' for item in result.events))

    def test_resume_clear_slash_forks_new_session(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='历史回答',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=2, output_tokens=1),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        first = agent.run('历史问题')
        original = self._load_session_snapshot(workspace, first.session_id or '')
        cleared = agent.resume('/clear', original)
        cleared_stored = self._load_session_snapshot(workspace, cleared.session_id or '')
        original_stored = self._load_session_snapshot(workspace, first.session_id or '')

        self.assertEqual(len(fake_client.calls), 1)
        self.assertNotEqual(cleared.session_id, first.session_id)
        self.assertEqual(cleared.stop_reason, 'slash_command')
        self.assertEqual(cleared.turns, 0)
        self.assertEqual(cleared.tool_calls, 0)
        self.assertEqual(cleared.transcript, ())
        self.assertEqual(cleared_stored.messages, ())
        self.assertEqual(cleared_stored.transcript, ())
        self.assertEqual(cleared_stored.turns, 0)
        self.assertEqual(cleared_stored.tool_calls, 0)
        self.assertEqual(original_stored.messages, original.messages)
        self.assertIn('Previous session id:', cleared.final_output)
        self.assertIn('Cleared session id:', cleared.final_output)

    def test_run_single_tool_call_chain(self) -> None:
        workspace = _make_test_dir()
        (workspace / 'demo.txt').write_text('hello', encoding='utf-8')

        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(id='call_1', name='read_file', arguments={'path': 'demo.txt'}),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=4, output_tokens=1),
                ),
                OneTurnResponse(
                    content='我已经读完文件并总结。',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=3, output_tokens=4),
                ),
            ]
        )
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('读取 demo.txt 并总结')
        stored = self._load_session_snapshot(workspace, result.session_id or '')
        self.assertEqual(len(list((workspace / 'sessions').glob('*.json'))), 1)

        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.final_output, '我已经读完文件并总结。')
        self.assertEqual(len(fake_client.calls), 2)
        self.assertEqual(stored.turns, 2)
        self.assertEqual(stored.tool_calls, 1)
        self.assertEqual(stored.usage.input_tokens, 7)
        self.assertEqual(stored.usage.output_tokens, 5)
        self.assertEqual(stored.messages[0]['content'], '读取 demo.txt 并总结')

        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        self.assertEqual(tool_rows[0].get('tool_call_id'), 'call_1')
        self.assertIn('hello', tool_rows[0].get('content', ''))

    def test_run_multiple_tool_calls_in_one_turn(self) -> None:
        workspace = _make_test_dir()
        (workspace / 'note.txt').write_text('line1\nline2\n', encoding='utf-8')

        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(id='call_1', name='list_dir', arguments={'path': '.'}),
                        ToolCall(id='call_2', name='read_file', arguments={'path': 'note.txt'}),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=5, output_tokens=1),
                ),
                OneTurnResponse(
                    content='已经完成目录与文件读取。',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ]
        )
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('先看目录，再看 note.txt')

        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls, 2)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.final_output, '已经完成目录与文件读取。')

        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 2)
        self.assertEqual({item.get('tool_call_id') for item in tool_rows}, {'call_1', 'call_2'})

    def test_run_loads_virtual_tool_from_workspace_plugin_manifest(self) -> None:
        workspace = _make_test_dir()
        manifest_dir = workspace / '.claw' / 'plugins'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / 'demo.json').write_text(
            json.dumps(
                {
                    'name': 'demo-plugin',
                    'summary': 'Expose a workspace banner tool.',
                    'virtual_tools': [
                        {
                            'name': 'workspace_banner',
                            'description': 'Return a fixed banner.',
                            'content': 'Banner from plugin runtime.',
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(id='call_1', name='workspace_banner', arguments={}),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=3, output_tokens=1),
                ),
                OneTurnResponse(
                    content='插件工具执行完成。',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ]
        )

        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('调用 workspace_banner')

        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.final_output, '插件工具执行完成。')
        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        self.assertIn('Banner from plugin runtime.', tool_rows[0].get('content', ''))

    def test_run_policy_budget_override_applies_before_first_model_call(self) -> None:
        workspace = _make_test_dir()
        manifest_dir = workspace / '.claw' / 'policies'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / 'budget.json').write_text(
            json.dumps(
                {
                    'name': 'budget-policy',
                    'trusted': True,
                    'budget_overrides': {'max_model_calls': 0},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        fake_client = _FakeOpenAIClient([])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        result = agent.run('这次不应触发模型调用')

        self.assertEqual(result.stop_reason, 'model_call_limit')
        self.assertEqual(len(fake_client.calls), 0)
        self.assertEqual(agent.runtime_config.budget_config.max_model_calls, 0)

    def test_run_policy_deny_filters_tool_registry(self) -> None:
        workspace = _make_test_dir()
        (workspace / 'demo.txt').write_text('hello', encoding='utf-8')
        manifest_dir = workspace / '.claw' / 'policies'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / 'deny.json').write_text(
            json.dumps(
                {
                    'name': 'deny-policy',
                    'trusted': True,
                    'deny_tools': ['read_file'],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(id='call_1', name='read_file', arguments={'path': 'demo.txt'}),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=4, output_tokens=1),
                ),
                OneTurnResponse(
                    content='已处理 deny 结果。',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ]
        )

        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('尝试读取 demo.txt')

        self.assertNotIn('read_file', agent.tool_registry)
        self.assertEqual(result.stop_reason, 'stop')
        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        self.assertIn('blocked by policy', tool_rows[0].get('content', ''))
        self.assertEqual(tool_rows[0].get('metadata', {}).get('blocked_by'), 'policy')

    def test_run_plugin_block_in_tool_pipeline(self) -> None:
        workspace = _make_test_dir()
        (workspace / 'demo.txt').write_text('hello', encoding='utf-8')
        manifest_dir = workspace / '.claw' / 'plugins'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / 'blocker.json').write_text(
            json.dumps(
                {
                    'name': 'plugin-blocker',
                    'summary': 'Block read_file in tool pipeline.',
                    'deny_tools': ['read_file'],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(id='call_1', name='read_file', arguments={'path': 'demo.txt'}),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=4, output_tokens=1),
                ),
                OneTurnResponse(
                    content='插件阻断已处理。',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ]
        )

        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('尝试调用 read_file')

        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        self.assertIn('blocked by plugin', tool_rows[0].get('content', ''))
        self.assertEqual(tool_rows[0].get('metadata', {}).get('blocked_by'), 'plugin')
        self.assertTrue(any(item.get('type') == 'tool_blocked' and item.get('source') == 'plugin' for item in result.events))

    def test_run_tool_pipeline_injects_plugin_and_policy_messages(self) -> None:
        workspace = _make_test_dir()
        plugin_dir = workspace / '.claw' / 'plugins'
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / 'hooks.json').write_text(
            json.dumps(
                {
                    'name': 'plugin-hooks',
                    'summary': 'Inject plugin pre/post messages.',
                    'before_hooks': [{'kind': 'message', 'content': 'plugin before'}],
                    'after_hooks': [{'kind': 'message', 'content': 'plugin after'}],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        policy_dir = workspace / '.claw' / 'policies'
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / 'hooks.json').write_text(
            json.dumps(
                {
                    'name': 'policy-hooks',
                    'trusted': True,
                    'before_hooks': [{'kind': 'message', 'content': 'policy before'}],
                    'after_hooks': [{'kind': 'message', 'content': 'policy after'}],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(id='call_1', name='list_dir', arguments={'path': '.'}),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=3, output_tokens=1),
                ),
                OneTurnResponse(
                    content='双重注入完成。',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ]
        )

        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('执行 list_dir 并观测 hook 注入')

        system_rows = [item for item in result.transcript if item.get('role') == 'system']
        self.assertEqual([item.get('content') for item in system_rows], ['plugin before', 'policy before', 'plugin after', 'policy after'])

        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        self.assertEqual(tool_rows[0].get('metadata', {}).get('preflight_sources'), ['plugin', 'policy'])
        self.assertEqual(tool_rows[0].get('metadata', {}).get('after_hook_sources'), ['plugin', 'policy'])
        self.assertEqual(len([item for item in result.events if item.get('type') == 'tool_preflight']), 2)
        self.assertEqual(len([item for item in result.events if item.get('type') == 'tool_after_hook']), 2)

    def test_run_delegate_agent_executes_children_and_records_group_summary(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(
                            id='delegate_1',
                            name='delegate_agent',
                            arguments={
                                'label': 'demo-group',
                                'tasks': [
                                    {'task_id': 'task-a', 'prompt': '执行子任务 A'},
                                    {
                                        'task_id': 'task-b',
                                        'prompt': '执行子任务 B',
                                        'dependencies': ['task-a'],
                                    },
                                ],
                            },
                        ),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=4, output_tokens=1),
                ),
                OneTurnResponse(
                    content='子任务 A 完成',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=1),
                ),
                OneTurnResponse(
                    content='子任务 B 完成',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=1),
                ),
                OneTurnResponse(
                    content='父任务汇总完成',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ]
        )

        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('委托两个子任务并汇总')

        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.final_output, '父任务汇总完成')
        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        group_summary = tool_rows[0].get('metadata', {}).get('group_summary', {})
        self.assertEqual(group_summary.get('child_count'), 2)
        self.assertEqual(group_summary.get('completed_children'), 2)
        self.assertEqual(group_summary.get('failed_children'), 0)
        self.assertEqual(group_summary.get('stop_reason_counts', {}).get('stop'), 2)
        self.assertTrue(any(item.get('type') == 'delegate_group_start' for item in result.events))
        self.assertEqual(len([item for item in result.events if item.get('type') == 'delegate_child_complete']), 2)
        self.assertTrue(any(item.get('type') == 'delegate_group_complete' for item in result.events))

    def test_run_delegate_agent_skips_dependents_after_child_failure(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(
                            id='delegate_1',
                            name='delegate_agent',
                            arguments={
                                'tasks': [
                                    {'task_id': 'task-a', 'prompt': '执行子任务 A'},
                                    {
                                        'task_id': 'task-b',
                                        'prompt': '执行子任务 B',
                                        'dependencies': ['task-a'],
                                    },
                                ],
                            },
                        ),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=4, output_tokens=1),
                ),
                OpenAIConnectionError('child network down'),
                OneTurnResponse(
                    content='父任务收到失败摘要',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ]
        )

        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('委托两个子任务，其中第二个依赖第一个')

        self.assertEqual(result.stop_reason, 'stop')
        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        group_summary = tool_rows[0].get('metadata', {}).get('group_summary', {})
        self.assertEqual(group_summary.get('failed_children'), 1)
        self.assertEqual(group_summary.get('dependency_skips'), 1)
        self.assertEqual(group_summary.get('stop_reason_counts', {}).get('backend_error'), 1)
        self.assertEqual(group_summary.get('stop_reason_counts', {}).get('dependency_skipped'), 1)
        self.assertTrue(any(item.get('type') == 'delegate_child_skipped' for item in result.events))
        self.assertTrue(any(item.get('type') == 'delegate_group_complete' for item in result.events))

    def test_run_delegate_agent_stops_when_max_delegated_tasks_would_be_exceeded(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(
                            id='delegate_1',
                            name='delegate_agent',
                            arguments={
                                'tasks': [
                                    {'task_id': 'task-a', 'prompt': '执行子任务 A'},
                                    {'task_id': 'task-b', 'prompt': '执行子任务 B'},
                                ],
                            },
                        ),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=4, output_tokens=1),
                ),
            ]
        )

        agent = self._build_agent(
            fake_client,
            self._build_budget_config(workspace, BudgetConfig(max_delegated_tasks=1)),
        )
        result = agent.run('委托两个子任务，但预算只允许一个')

        self.assertEqual(result.stop_reason, 'delegated_task_limit')
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(len(fake_client.calls), 1)
        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        self.assertEqual(tool_rows[0].get('metadata', {}).get('error_kind'), 'delegated_task_limit')
        self.assertTrue(any(item.get('type') == 'delegate_group_blocked' for item in result.events))
        self.assertTrue(any(item.get('type') == 'budget_stop' and item.get('reason') == 'delegated_task_limit' for item in result.events))

    def test_run_stops_with_max_turns(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient(
            [
                OneTurnResponse(
                    content='继续执行',
                    tool_calls=(
                        ToolCall(id='call_1', name='list_dir', arguments={'path': '.'}),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=3, output_tokens=1),
                ),
            ]
        )
        config = self._build_runtime_config(workspace, max_turns=1)
        agent = self._build_agent(fake_client, config)
        result = agent.run('持续执行直到停止')
        stored = self._load_session_snapshot(workspace, result.session_id or '')

        self.assertEqual(result.turns, 1)
        self.assertEqual(result.stop_reason, 'max_turns')
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(stored.stop_reason, 'max_turns')
        self.assertEqual(stored.turns, 1)
        self.assertEqual(stored.tool_calls, 1)

    def test_run_returns_backend_error_when_model_call_fails(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([OpenAIConnectionError('network down')])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('测试后端异常')
        stored = self._load_session_snapshot(workspace, result.session_id or '')

        self.assertEqual(result.turns, 1)
        self.assertEqual(result.stop_reason, 'backend_error')
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.final_output, '')
        self.assertEqual(len(result.transcript), 1)  # 只有初始 user
        self.assertEqual(stored.stop_reason, 'backend_error')
        self.assertEqual(stored.messages, ({'role': 'user', 'content': '测试后端异常'},))
        self.assertEqual(stored.transcript, ({'role': 'user', 'content': '测试后端异常'},))
        self.assertTrue(any(item.get('type') == 'backend_error' for item in result.events))

    # ------------------------------------------------------------------
    # ISSUE-008 Resume 连续执行与状态继承
    # ------------------------------------------------------------------

    def test_resume_session_id_does_not_drift(self) -> None:
        """resume 后 session_id 必须与第一次 run 的保持一致。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='第一轮回答',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=3, output_tokens=2),
            ),
            OneTurnResponse(
                content='第二轮回答',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=4, output_tokens=3),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        first = agent.run('第一个问题')
        stored = self._load_session_snapshot(workspace, first.session_id or '')
        second = agent.resume('第二个问题', stored)

        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(second.final_output, '第二轮回答')

    def test_resume_accumulates_usage_turns_and_tool_calls(self) -> None:
        """resume 后 usage / turns / tool_calls 应为两次执行的累计值。"""
        workspace = _make_test_dir()
        (workspace / 'note.txt').write_text('data', encoding='utf-8')
        fake_client = _FakeOpenAIClient([
            # 第一次 run：直接返回，usage 10+5
            OneTurnResponse(
                content='第一轮',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            ),
            # 第二次 resume：调用一次工具后返回，usage 4+1 + 3+4
            OneTurnResponse(
                content='',
                tool_calls=(ToolCall(id='c1', name='read_file', arguments={'path': 'note.txt'}),),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=4, output_tokens=1),
            ),
            OneTurnResponse(
                content='第二轮',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=3, output_tokens=4),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        first = agent.run('任务一')
        stored = self._load_session_snapshot(workspace, first.session_id or '')
        second = agent.resume('任务二', stored)

        self.assertEqual(second.turns, 3)          # 1 + 2
        self.assertEqual(second.tool_calls, 1)     # 0 + 1
        self.assertEqual(second.usage.input_tokens, 17)   # 10+4+3
        self.assertEqual(second.usage.output_tokens, 10)  # 5+1+4

        # 落盘后的累计值同样一致
        restored2 = self._load_session_snapshot(workspace, second.session_id or '')
        self.assertEqual(restored2.turns, 3)
        self.assertEqual(restored2.usage.input_tokens, 17)

    def test_resume_model_sees_history_context(self) -> None:
        """resume 时模型请求应包含第一轮的历史消息（上下文连续）。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='历史回答',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=2, output_tokens=1),
            ),
            OneTurnResponse(
                content='续跑回答',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=3, output_tokens=2),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        first = agent.run('历史问题')
        stored = self._load_session_snapshot(workspace, first.session_id or '')
        agent.resume('续跑问题', stored)

        # fake_client.calls[1] 是 resume 时发出的请求消息列表
        self.assertEqual(len(fake_client.calls), 2)
        resume_messages = fake_client.calls[1]
        contents = [m.get('content', '') for m in resume_messages]

        # 历史回答与续跑问题都要出现在第二次调用的消息里
        self.assertIn('历史回答', contents)
        self.assertIn('续跑问题', contents)

    def test_resume_backend_error_preserves_session_id_and_saves(self) -> None:
        """resume 发生 backend_error 时 session_id 不变，且仍落盘。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='成功回答',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=5, output_tokens=2),
            ),
            OpenAIConnectionError('network down'),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        first = agent.run('任务一')
        stored = self._load_session_snapshot(workspace, first.session_id or '')
        second = agent.resume('任务二', stored)

        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(second.stop_reason, 'backend_error')

        # 落盘文件应仍可读取且 session_id 一致
        restored2 = self._load_session_snapshot(workspace, second.session_id or '')
        self.assertEqual(restored2.session_id, first.session_id)
        self.assertEqual(restored2.stop_reason, 'backend_error')

    # ------------------------------------------------------------------
    # ISSUE-009 预算闸门集成测试
    # ------------------------------------------------------------------

    def _build_budget_config(self, workspace: Path, budget: BudgetConfig) -> AgentRuntimeConfig:
        """构造带自定义预算的运行配置。"""
        return AgentRuntimeConfig(
            cwd=workspace,
            max_turns=6,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(allow_file_write=True),
            budget_config=budget,
        )

    def test_run_stops_on_token_limit(self) -> None:
        """max_input_tokens=1 时，第一轮 token preflight 应触发 token_limit。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(content='ok', tool_calls=(), finish_reason='stop', usage=TokenUsage()),
        ])
        agent = self._build_agent(
            fake_client,
            self._build_budget_config(workspace, BudgetConfig(max_input_tokens=1)),
        )
        result = agent.run('任务')
        self.assertEqual(result.stop_reason, 'token_limit')
        # token 硬超限时不应进行模型调用
        self.assertEqual(len(fake_client.calls), 0)

    def test_run_stops_on_cost_limit(self) -> None:
        """max_total_cost_usd=0.0 时，成本检查（0.0 >= 0.0）应触发 cost_limit。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(content='ok', tool_calls=(), finish_reason='stop', usage=TokenUsage()),
        ])
        agent = self._build_agent(
            fake_client,
            self._build_budget_config(workspace, BudgetConfig(max_total_cost_usd=0.0)),
        )
        result = agent.run('任务')
        self.assertEqual(result.stop_reason, 'cost_limit')
        # 成本超限应在模型调用前触发
        self.assertEqual(len(fake_client.calls), 0)

    def test_run_stops_on_tool_call_limit(self) -> None:
        """max_tool_calls=1 时，第一个工具执行后应触发 tool_call_limit。"""
        workspace = _make_test_dir()
        (workspace / 'f.txt').write_text('hello', encoding='utf-8')
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='',
                tool_calls=(
                    ToolCall(id='t1', name='read_file', arguments={'path': 'f.txt'}),
                    ToolCall(id='t2', name='read_file', arguments={'path': 'f.txt'}),
                ),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=3, output_tokens=1),
            ),
            OneTurnResponse(content='done', tool_calls=(), finish_reason='stop', usage=TokenUsage()),
        ])
        agent = self._build_agent(
            fake_client,
            self._build_budget_config(workspace, BudgetConfig(max_tool_calls=1)),
        )
        result = agent.run('读文件')
        self.assertEqual(result.stop_reason, 'tool_call_limit')
        # 执行 1 个工具后触发，tool_calls 计数应为 1
        self.assertEqual(result.tool_calls, 1)
        # 只发生了 1 次模型调用（第 2 个工具没执行，不需要第 2 次模型调用）
        self.assertEqual(len(fake_client.calls), 1)

    def test_run_stops_on_model_call_limit(self) -> None:
        """max_model_calls=1 时，第 2 轮开始前应触发 model_call_limit。"""
        workspace = _make_test_dir()
        (workspace / 'f.txt').write_text('data', encoding='utf-8')
        fake_client = _FakeOpenAIClient([
            # 第 1 次模型调用：返回工具请求，迫使进入第 2 轮
            OneTurnResponse(
                content='',
                tool_calls=(ToolCall(id='t1', name='read_file', arguments={'path': 'f.txt'}),),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=3, output_tokens=1),
            ),
            # 第 2 次模型调用永远不应被触发
            OneTurnResponse(content='done', tool_calls=(), finish_reason='stop', usage=TokenUsage()),
        ])
        agent = self._build_agent(
            fake_client,
            self._build_budget_config(workspace, BudgetConfig(max_model_calls=1)),
        )
        result = agent.run('任务')
        self.assertEqual(result.stop_reason, 'model_call_limit')
        # 只应发生 1 次模型调用
        self.assertEqual(len(fake_client.calls), 1)

    def test_run_stops_on_session_turns_limit_with_offset(self) -> None:
        """resume 场景：turns_offset=3, max_session_turns=3，第一轮就应触发 session_turns_limit。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(content='ok', tool_calls=(), finish_reason='stop', usage=TokenUsage()),
        ])
        config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=6,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(allow_file_write=True),
            budget_config=BudgetConfig(max_session_turns=3),
        )
        agent = self._build_agent(fake_client, config)
        # 伪造一个已经用了 3 轮的历史会话
        stored = AgentSessionSnapshot(
            session_id='test-session-999',
            model_config=fake_client.model_config,
            runtime_config=config,
            messages=({'role': 'user', 'content': '历史消息'},),
            turns=3,
        )
        result = agent.resume('继续', stored)
        self.assertEqual(result.stop_reason, 'session_turns_limit')
        # turns_limit 应在模型调用前触发
        self.assertEqual(len(fake_client.calls), 0)

    def test_snip_triggered_on_soft_over(self) -> None:
        """max_input_tokens 极小时 is_soft_over=True，应触发 snip_boundary 事件。"""
        workspace = _make_test_dir()
        (workspace / 'f.txt').write_text('data', encoding='utf-8')
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='',
                tool_calls=(ToolCall(id='t1', name='read_file', arguments={'path': 'f.txt'}),),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=5, output_tokens=1),
            ),
            OneTurnResponse(content='done', tool_calls=(), finish_reason='stop', usage=TokenUsage()),
        ])
        # max_input_tokens=5000：soft_limit=0（任意 projected>0 → soft_over=True）
        # 同时 hard_limit=904，几条消息的投影 token << 904，不触发 hard_over
        config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(allow_file_write=True),
            budget_config=BudgetConfig(max_input_tokens=5000),
            compact_preserve_messages=1,   # 确保消息不全在尾部，snip 有候选目标
        )
        agent = self._build_agent(fake_client, config)
        result = agent.run('任务')
        snip_events = [e for e in result.events if e.get('type') == 'snip_boundary']
        # 至少发生一次 snip（第 2 轮或更晚的轮次，消息已有工具结果可剪）
        self.assertGreater(len(snip_events), 0)
        self.assertIn('snipped_count', snip_events[0])
        self.assertIn('tokens_removed', snip_events[0])

    def test_no_snip_when_not_soft_over(self) -> None:
        """max_input_tokens 不设置时 is_soft_over 永远为 False，不应有 snip_boundary。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(content='done', tool_calls=(), finish_reason='stop', usage=TokenUsage()),
        ])
        config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(allow_file_write=True),
            # 不设置 max_input_tokens → soft_over 永远 False
        )
        agent = self._build_agent(fake_client, config)
        result = agent.run('任务')
        snip_events = [e for e in result.events if e.get('type') == 'snip_boundary']
        self.assertEqual(len(snip_events), 0)

    def test_auto_compact_triggered_at_explicit_threshold(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='用户目标：继续当前任务\n下一步：回答最新请求',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=2, output_tokens=1),
            ),
            OneTurnResponse(
                content='done',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=3, output_tokens=4),
            ),
        ])
        config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(allow_file_write=True),
            auto_compact_threshold_tokens=1,
            compact_preserve_messages=1,
        )
        agent = self._build_agent(fake_client, config)
        stored = AgentSessionSnapshot(
            session_id='compact-session-001',
            model_config=fake_client.model_config,
            runtime_config=config,
            messages=(
                {'role': 'user', 'content': '旧需求 ' * 80},
                {'role': 'assistant', 'content': '旧回答 ' * 80},
                {'role': 'tool', 'tool_call_id': 't1', 'name': 'read_file', 'content': '旧工具输出 ' * 80},
            ),
            turns=1,
        )

        result = agent.resume('继续处理当前任务', stored)

        compact_events = [
            e for e in result.events
            if e.get('type') == 'compact_boundary' and e.get('trigger') == 'auto'
        ]
        self.assertGreater(len(compact_events), 0)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(len(fake_client.calls), 2)
        self.assertEqual(result.usage.input_tokens, 5)
        self.assertEqual(result.usage.output_tokens, 5)
        self.assertTrue(any(
            'Compact summary of earlier conversation' in item.get('content', '')
            for item in fake_client.calls[1]
        ))

    def test_auto_compact_not_triggered_when_threshold_not_met(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(content='done', tool_calls=(), finish_reason='stop', usage=TokenUsage()),
        ])
        config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(allow_file_write=True),
            auto_compact_threshold_tokens=10_000,
            compact_preserve_messages=1,
        )
        agent = self._build_agent(fake_client, config)
        stored = AgentSessionSnapshot(
            session_id='compact-session-002',
            model_config=fake_client.model_config,
            runtime_config=config,
            messages=(
                {'role': 'user', 'content': '旧需求'},
                {'role': 'assistant', 'content': '旧回答'},
                {'role': 'tool', 'tool_call_id': 't1', 'name': 'read_file', 'content': '旧工具输出'},
            ),
            turns=1,
        )

        result = agent.resume('继续处理当前任务', stored)

        compact_events = [e for e in result.events if e.get('type') == 'compact_boundary']
        self.assertEqual(len(compact_events), 0)
        self.assertEqual(len(fake_client.calls), 1)
        self.assertEqual(result.stop_reason, 'stop')

    def test_reactive_compact_retries_on_context_length_error(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OpenAIResponseError(
                'HTTP 400 from model backend: maximum context length exceeded',
                status_code=400,
                detail='maximum context length exceeded',
            ),
            OneTurnResponse(
                content='用户目标：继续当前任务\n下一步：回答最新请求',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=2, output_tokens=1),
            ),
            OneTurnResponse(
                content='done',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=3, output_tokens=2),
            ),
        ])
        config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(allow_file_write=True),
            compact_preserve_messages=1,
        )
        agent = self._build_agent(fake_client, config)
        stored = AgentSessionSnapshot(
            session_id='compact-session-003',
            model_config=fake_client.model_config,
            runtime_config=config,
            messages=(
                {'role': 'user', 'content': '旧需求 ' * 80},
                {'role': 'assistant', 'content': '旧回答 ' * 80},
                {'role': 'tool', 'tool_call_id': 't1', 'name': 'read_file', 'content': '旧工具输出 ' * 80},
            ),
            turns=1,
        )

        result = agent.resume('继续处理当前任务', stored)

        retry_events = [e for e in result.events if e.get('type') == 'reactive_compact_retry']
        self.assertEqual(len(retry_events), 1)
        self.assertTrue(retry_events[0].get('ok'))
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(len(fake_client.calls), 3)
        self.assertEqual(result.usage.input_tokens, 5)
        self.assertEqual(result.usage.output_tokens, 3)

    def test_reactive_compact_returns_backend_error_when_compaction_fails(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OpenAIResponseError(
                'HTTP 400 from model backend: maximum context length exceeded',
                status_code=400,
                detail='maximum context length exceeded',
            ),
            OneTurnResponse(
                content='   ',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=2, output_tokens=1),
            ),
        ])
        config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(allow_file_write=True),
            compact_preserve_messages=1,
        )
        agent = self._build_agent(fake_client, config)
        stored = AgentSessionSnapshot(
            session_id='compact-session-004',
            model_config=fake_client.model_config,
            runtime_config=config,
            messages=(
                {'role': 'user', 'content': '旧需求 ' * 80},
                {'role': 'assistant', 'content': '旧回答 ' * 80},
                {'role': 'tool', 'tool_call_id': 't1', 'name': 'read_file', 'content': '旧工具输出 ' * 80},
            ),
            turns=1,
        )

        result = agent.resume('继续处理当前任务', stored)

        retry_events = [e for e in result.events if e.get('type') == 'reactive_compact_retry']
        self.assertEqual(len(retry_events), 1)
        self.assertFalse(retry_events[0].get('ok'))
        self.assertEqual(result.stop_reason, 'backend_error')
        self.assertEqual(len(fake_client.calls), 2)

    def test_run_calls_workspace_search_tool_from_main_loop(self) -> None:
        """验证 workspace_search 工具在主循环被正确调用。"""
        workspace = _make_test_dir()
        
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='',
                tool_calls=(
                    ToolCall(
                        id='call-1',
                        name='workspace_search',
                        arguments={'query': 'hello'},
                    ),
                ),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=2, output_tokens=3),
            ),
            OneTurnResponse(
                content='搜索完成',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=5, output_tokens=2),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        
        # Mock 搜索 runtime，让它有一个 provider 以便工具被注册
        mock_provider = SearchProviderProfile(
            provider_id='test_provider',
            provider='test',
            title='Test Provider',
            base_url='http://test.com',
            source_path=Path('test.json'),
        )
        agent.search_runtime.providers = [mock_provider]
        
        # 重新注册工具以应用模拟的 provider
        agent.tool_registry = agent._register_workspace_runtime_tools(agent.tool_registry)
        
        # Mock 搜索方法以返回结果对象
        mock_result = SearchResult(
            title='test',
            url='http://test.com',
            snippet='test snippet',
            provider_id='test_provider',
            rank=1,
        )
        mock_response = SearchResponse(
            provider=mock_provider,
            query='hello',
            results=(mock_result,),
            attempts=1,
        )
        agent.search_runtime.search = mock.Mock(return_value=mock_response)
        
        result = agent.run('搜索一下内容')
        
        # 验证：工具被调用且工作流完整
        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.final_output, '搜索完成')
        agent.search_runtime.search.assert_called_once()

    def test_run_calls_mcp_search_capabilities_from_main_loop(self) -> None:
        """验证主循环通过 mcp_search_capabilities 搜索远端能力。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='',
                tool_calls=(
                    ToolCall(
                        id='call-1',
                        name='mcp_search_capabilities',
                        arguments={
                            'server_name': 'tavily',
                            'query': 'today tech news',
                        },
                    ),
                ),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=2, output_tokens=3),
            ),
            OneTurnResponse(
                content='MCP 查询完成',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=5, output_tokens=2),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        agent.mcp_runtime.servers = [mock.Mock(name='tavily')]
        agent.mcp_runtime.search_capabilities = mock.Mock(return_value=(MCPCapability(
            handle='mcp:tavily:tavily_search',
            tool_name='tavily_search',
            server_name='tavily',
            description='Search the web for current information.',
            required_parameters=('query',),
            parameter_summary=('query:string',),
            risk_level='read',
        ),))
        agent.mcp_runtime.render_capability_index = mock.Mock(return_value='- mcp:tavily:tavily_search')

        agent.tool_registry = agent._register_workspace_runtime_tools(agent.tool_registry)

        result = agent.run('查询适合当前任务的 MCP 能力')

        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.final_output, 'MCP 查询完成')
        agent.mcp_runtime.search_capabilities.assert_called_once_with(
            query='today tech news',
            server_name='tavily',
            limit=20,
        )
        agent.mcp_runtime.render_capability_index.assert_called_once_with(
            query='today tech news',
            server_name='tavily',
            limit=20,
        )

    def test_run_calls_mcp_call_tool_from_main_loop(self) -> None:
        """验证主循环通过 capability_handle 调用远端 MCP 工具。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='',
                tool_calls=(
                    ToolCall(
                        id='call-1',
                        name='mcp_call_tool',
                        arguments={
                            'capability_handle': 'mcp:tavily:tavily_search',
                            'arguments': {'query': 'today tech news'},
                        },
                    ),
                ),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=2, output_tokens=3),
            ),
            OneTurnResponse(
                content='MCP 查询完成',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=5, output_tokens=2),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        agent.mcp_runtime.servers = [mock.Mock(name='tavily')]
        agent.mcp_runtime.resolve_capability = mock.Mock(return_value=MCPCapability(
            handle='mcp:tavily:tavily_search',
            tool_name='tavily_search',
            server_name='tavily',
            description='Search the web for current information.',
            required_parameters=('query',),
            parameter_summary=('query:string',),
            risk_level='read',
        ))
        agent.mcp_runtime.resolve_tool = mock.Mock(return_value=MCPTool(
            name='tavily_search',
            server_name='tavily',
            description='Search the web for current information.',
            input_schema={
                'type': 'object',
                'properties': {'query': {'type': 'string'}},
                'required': ['query'],
            },
        ))
        agent.mcp_runtime.call_tool = mock.Mock(return_value=MCPToolCallResult(
            server_name='tavily',
            tool_name='tavily_search',
            content='headline-1',
            is_error=False,
        ))

        agent.tool_registry = agent._register_workspace_runtime_tools(agent.tool_registry)

        result = agent.run('查询今天的科技新闻')

        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.final_output, 'MCP 查询完成')
        agent.mcp_runtime.resolve_capability.assert_called_once_with('mcp:tavily:tavily_search')
        agent.mcp_runtime.resolve_tool.assert_called_once_with('tavily_search', server_name='tavily')
        agent.mcp_runtime.call_tool.assert_called_once_with(
            'tavily_search',
            arguments={'query': 'today tech news'},
            server_name='tavily',
            max_chars=agent.runtime_config.max_output_chars,
        )

    def test_capability_search_materializes_temporary_mcp_tool_for_next_turn(self) -> None:
        """验证 capability search 会把少量候选物化为下一轮可见的临时工具。"""
        workspace = _make_test_dir()
        capability_handle = 'mcp:tavily:tavily_search'
        materialized_tool_name = 'mcp_cap_mcp_tavily_tavily_search'
        fake_client = _FakeOpenAIClient([
            OneTurnResponse(
                content='',
                tool_calls=(
                    ToolCall(
                        id='call-1',
                        name='mcp_search_capabilities',
                        arguments={'query': 'today tech news', 'server_name': 'tavily'},
                    ),
                ),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=2, output_tokens=1),
            ),
            OneTurnResponse(
                content='',
                tool_calls=(
                    ToolCall(
                        id='call-2',
                        name=materialized_tool_name,
                        arguments={'query': 'today tech news'},
                    ),
                ),
                finish_reason='tool_calls',
                usage=TokenUsage(input_tokens=3, output_tokens=1),
            ),
            OneTurnResponse(
                content='临时工具调用完成',
                tool_calls=(),
                finish_reason='stop',
                usage=TokenUsage(input_tokens=2, output_tokens=2),
            ),
        ])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))

        capability = MCPCapability(
            handle=capability_handle,
            tool_name='tavily_search',
            server_name='tavily',
            description='Search the web for current information.',
            required_parameters=('query',),
            parameter_summary=('query:string',),
            risk_level='read',
        )
        remote_tool = MCPTool(
            name='tavily_search',
            server_name='tavily',
            description='Search the web for current information.',
            input_schema={
                'type': 'object',
                'properties': {'query': {'type': 'string'}},
                'required': ['query'],
            },
        )

        agent.mcp_runtime.servers = [mock.Mock(name='tavily')]
        agent.mcp_runtime.search_capabilities = mock.Mock(return_value=(capability,))
        agent.mcp_runtime.render_capability_index = mock.Mock(return_value='- mcp:tavily:tavily_search')
        agent.mcp_runtime.resolve_capability = mock.Mock(return_value=capability)
        agent.mcp_runtime.resolve_tool = mock.Mock(return_value=remote_tool)
        agent.mcp_runtime.call_tool = mock.Mock(return_value=MCPToolCallResult(
            server_name='tavily',
            tool_name='tavily_search',
            content='headline-1',
            is_error=False,
        ))
        agent.tool_registry = agent._register_workspace_runtime_tools(agent.tool_registry)

        result = agent.run('查询今天的科技新闻并继续执行')

        self.assertEqual(result.final_output, '临时工具调用完成')
        self.assertEqual(len(fake_client.tools_seen), 3)
        second_turn_tools = [
            item.get('function', {}).get('name')
            for item in fake_client.tools_seen[1]
            if isinstance(item, dict)
        ]
        self.assertIn(materialized_tool_name, second_turn_tools)
        stored = self._load_session_snapshot(workspace, result.session_id or '')
        self.assertEqual(stored.materialized_mcp_capability_handles, (capability_handle,))
        self.assertEqual(len(stored.mcp_capability_shortlist), 1)

    def test_workspace_search_and_mcp_tools_registered_when_configured(self) -> None:
        """验证 MCP server 存在时启动只注册统一 façade 工具。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([])
        agent = self._build_agent(fake_client, self._build_runtime_config(workspace))
        
        # 配置 search provider 和 MCP 资源
        mock_search_provider = mock.Mock()
        mock_search_provider.provider_id = 'test_provider'
        agent.search_runtime.providers = [mock_search_provider]
        
        mock_mcp_resource = mock.Mock()
        agent.mcp_runtime.resources = [mock_mcp_resource]
        agent.mcp_runtime.servers = [mock.Mock(name='tavily'), mock.Mock(name='filesystem')]
        agent.mcp_runtime.list_tools = mock.Mock(side_effect=AssertionError('startup should not enumerate MCP tools'))
        
        # 重新注册工具
        agent.tool_registry = agent._register_workspace_runtime_tools(agent.tool_registry)
        
        # 验证工具被注册
        self.assertIn('workspace_search', agent.tool_registry)
        self.assertIn('mcp_list_resources', agent.tool_registry)
        self.assertIn('mcp_read_resource', agent.tool_registry)
        self.assertIn('mcp_search_capabilities', agent.tool_registry)
        self.assertIn('mcp_call_tool', agent.tool_registry)
        self.assertNotIn('mcp_list_tools', agent.tool_registry)
        self.assertNotIn('tavily_search', agent.tool_registry)
        self.assertNotIn('mcp_filesystem_read_file', agent.tool_registry)

    def test_mcp_call_tool_blocks_filesystem_writes_without_permission(self) -> None:
        """验证 mcp_call_tool 仍会拦截 filesystem 写工具。"""
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([])
        config = AgentRuntimeConfig(
            cwd=workspace,
            session_directory=workspace / 'sessions',
            permissions=AgentPermissions(
                allow_file_write=False,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            ),
        )
        agent = self._build_agent(fake_client, config)

        agent.mcp_runtime.servers = [mock.Mock(name='filesystem')]
        agent.mcp_runtime.resolve_tool = mock.Mock(return_value=MCPTool(
            name='write_file',
            server_name='filesystem',
            description='Create a new file or overwrite an existing file.',
            input_schema={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'content': {'type': 'string'},
                },
                'required': ['path', 'content'],
            },
        ))
        agent.mcp_runtime.call_tool = mock.Mock()
        agent.tool_registry = agent._register_workspace_runtime_tools(agent.tool_registry)

        context = agent.tool_service.build_context(config, tool_registry=agent.tool_registry)
        result = agent.tool_service.execute(
            agent.tool_registry,
            'mcp_call_tool',
            {
                'tool_name': 'write_file',
                'server_name': 'filesystem',
                'arguments': {'path': 'demo.txt', 'content': 'hello'},
            },
            context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'permission_denied')
        agent.mcp_runtime.resolve_tool.assert_called_once_with('write_file', server_name='filesystem')
        agent.mcp_runtime.call_tool.assert_not_called()



if __name__ == '__main__':
    unittest.main()
