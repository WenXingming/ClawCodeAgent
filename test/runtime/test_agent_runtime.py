"""ISSUE-006 LocalCodingAgent 最小闭环测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from core_contracts.config import AgentPermissions, AgentRuntimeConfig, BudgetConfig, ModelConfig
from core_contracts.protocol import OneTurnResponse, ToolCall
from core_contracts.usage import TokenUsage
from openai_client.openai_client import OpenAIClient, OpenAIConnectionError, OpenAIResponseError
from runtime.agent_runtime import LocalCodingAgent
from session.session_snapshot import AgentSessionSnapshot
from session.session_store import AgentSessionStore


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

    def complete(self, messages, tools=None, *, output_schema=None):  # type: ignore[override]
        self.calls.append([dict(item) for item in messages])
        if not self._responses:
            raise AssertionError('No prepared response left for test')

        current = self._responses.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class LocalCodingAgentTests(unittest.TestCase):
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

    def _build_agent(self, fake_client: OpenAIClient, config: AgentRuntimeConfig) -> LocalCodingAgent:
        return LocalCodingAgent(fake_client, config, AgentSessionStore(config.session_directory))

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


if __name__ == '__main__':
    unittest.main()
