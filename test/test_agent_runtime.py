"""ISSUE-006 LocalCodingAgent 最小闭环测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
            permissions=AgentPermissions(
                allow_file_write=True,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            ),
        )

    def test_run_without_tool_calls_returns_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
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

        self.assertEqual(result.final_output, '直接返回答案')
        self.assertEqual(result.turns, 1)
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.usage.input_tokens, 2)
        self.assertEqual(result.usage.output_tokens, 3)
        self.assertEqual(len(result.transcript), 2)  # user + assistant

    def test_run_single_tool_call_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
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

        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertEqual(result.final_output, '我已经读完文件并总结。')
        self.assertEqual(len(fake_client.calls), 2)

        tool_rows = [item for item in result.transcript if item.get('role') == 'tool']
        self.assertEqual(len(tool_rows), 1)
        self.assertEqual(tool_rows[0].get('tool_call_id'), 'call_1')
        self.assertIn('hello', tool_rows[0].get('content', ''))

    def test_run_multiple_tool_calls_in_one_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
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
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
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

        self.assertEqual(result.turns, 1)
        self.assertEqual(result.stop_reason, 'max_turns')
        self.assertEqual(result.tool_calls, 1)

    def test_run_returns_backend_error_when_model_call_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            fake_client = _FakeOpenAIClient([OpenAIConnectionError('network down')])
            agent = LocalCodingAgent(fake_client, self._build_runtime_config(workspace))
            result = agent.run('测试后端异常')

        self.assertEqual(result.turns, 1)
        self.assertEqual(result.stop_reason, 'backend_error')
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.final_output, '')
        self.assertEqual(len(result.transcript), 1)  # 只有初始 user
        self.assertTrue(any(item.get('type') == 'backend_error' for item in result.events))


if __name__ == '__main__':
    unittest.main()
