"""ISSUE-006 LocalCodingAgent 最小闭环测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from src.agent_runtime import LocalCodingAgent
from src.contract_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    ModelConfig,
    OneTurnResponse,
    TokenUsage,
    ToolCall,
)
from src.openai_client import OpenAIClient, OpenAIConnectionError
from src.session_store import load_agent_session


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
        agent = LocalCodingAgent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('你好')
        stored = load_agent_session(result.session_id or '', directory=workspace / 'sessions')
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
        agent = LocalCodingAgent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('读取 demo.txt 并总结')
        stored = load_agent_session(result.session_id or '', directory=workspace / 'sessions')
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
        agent = LocalCodingAgent(fake_client, self._build_runtime_config(workspace))
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
        agent = LocalCodingAgent(fake_client, config)
        result = agent.run('持续执行直到停止')
        stored = load_agent_session(result.session_id or '', directory=workspace / 'sessions')

        self.assertEqual(result.turns, 1)
        self.assertEqual(result.stop_reason, 'max_turns')
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(stored.stop_reason, 'max_turns')
        self.assertEqual(stored.turns, 1)
        self.assertEqual(stored.tool_calls, 1)

    def test_run_returns_backend_error_when_model_call_fails(self) -> None:
        workspace = _make_test_dir()
        fake_client = _FakeOpenAIClient([OpenAIConnectionError('network down')])
        agent = LocalCodingAgent(fake_client, self._build_runtime_config(workspace))
        result = agent.run('测试后端异常')
        stored = load_agent_session(result.session_id or '', directory=workspace / 'sessions')

        self.assertEqual(result.turns, 1)
        self.assertEqual(result.stop_reason, 'backend_error')
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.final_output, '')
        self.assertEqual(len(result.transcript), 1)  # 只有初始 user
        self.assertEqual(stored.stop_reason, 'backend_error')
        self.assertEqual(stored.messages, ({'role': 'user', 'content': '测试后端异常'},))
        self.assertEqual(stored.transcript, ({'role': 'user', 'content': '测试后端异常'},))
        self.assertTrue(any(item.get('type') == 'backend_error' for item in result.events))


if __name__ == '__main__':
    unittest.main()
