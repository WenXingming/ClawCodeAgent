"""AgentGateway 最小闭环单元测试。

使用 Mock 隔离 ModelClient 与 ToolsGateway，
验证工具调用循环的边界、异常与主流程行为。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

from agent import AgentGateway
from core_contracts.config import (
    BudgetConfig,
    ExecutionPolicy,
    SessionPaths,
    WorkspaceScope,
)
from core_contracts.messaging import OneTurnResponse, ToolCall, ToolExecutionResult
from core_contracts.model import ModelClient, ModelConfig, ModelPricing
from core_contracts.primitives import TokenUsage
from core_contracts.session_contracts import AgentSessionSnapshot
from core_contracts.tools_contracts import ToolExecutionContext, ToolPermissionPolicy, ToolRegistry


def _make_token_usage(input_tokens: int = 10, output_tokens: int = 5) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def _make_text_response(content: str, finish_reason: str = 'stop') -> OneTurnResponse:
    return OneTurnResponse(
        content=content,
        tool_calls=(),
        finish_reason=finish_reason,
        usage=_make_token_usage(),
    )


def _make_tool_call_response(tool_calls: list[ToolCall]) -> OneTurnResponse:
    return OneTurnResponse(
        content='',
        tool_calls=tuple(tool_calls),
        finish_reason='tool_calls',
        usage=_make_token_usage(),
    )


def _make_workspace_scope() -> WorkspaceScope:
    return WorkspaceScope(cwd=Path(__file__).resolve().parent)


def _make_tools_gateway_mock() -> MagicMock:
    mock_gateway = MagicMock()
    mock_gateway.to_openai_tools.return_value = []
    mock_gateway.build_execution_context.return_value = ToolExecutionContext.build(
        _make_workspace_scope(),
        ExecutionPolicy(),
        ToolPermissionPolicy(),
        tool_registry=ToolRegistry.from_tools(),
    )
    return mock_gateway


class AgentGatewayTextOnlyTests(unittest.TestCase):
    """验证模型直接返回文本时循环立即退出。"""

    def test_run_returns_text_without_tool_calls(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        mock_client.complete.return_value = _make_text_response('Hello from model')

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=_make_tools_gateway_mock(),
            system_prompt='You are a helpful assistant.',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
        )

        result = gateway.run('Hi!')

        self.assertEqual(result.final_output, 'Hello from model')
        self.assertEqual(result.turns, 1)
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.stop_reason, 'stop')
        self.assertIsNotNone(result.session_id)
        mock_client.complete.assert_called_once()

    def test_run_passes_system_prompt_as_first_message(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        mock_client.complete.return_value = _make_text_response('ok')

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=_make_tools_gateway_mock(),
            system_prompt='SYSTEM: Be concise.',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
        )

        gateway.run('hello')

        messages = mock_client.complete.call_args.kwargs['messages']
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[0]['content'], 'SYSTEM: Be concise.')


class AgentGatewayToolCallTests(unittest.TestCase):
    """验证工具调用→执行→继续循环的完整流程。"""

    def test_executes_tool_then_returns_text(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        mock_client.complete.side_effect = [
            _make_tool_call_response([
                ToolCall(id='call_1', name='echo', arguments={'text': 'ping'})
            ]),
            _make_text_response('Tool done, result is pong'),
        ]

        mock_tools_gateway = _make_tools_gateway_mock()
        mock_tools_gateway.execute_tool.return_value = ToolExecutionResult(
            name='echo', ok=True, content='pong',
        )

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=mock_tools_gateway,
            system_prompt='You are helpful.',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
        )

        result = gateway.run('Echo ping please')

        self.assertEqual(result.final_output, 'Tool done, result is pong')
        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(mock_client.complete.call_count, 2)
        mock_tools_gateway.execute_tool.assert_called_once()

    def test_multiple_tool_calls_in_single_turn(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        mock_client.complete.side_effect = [
            _make_tool_call_response([
                ToolCall(id='c1', name='read', arguments={'path': 'a.txt'}),
                ToolCall(id='c2', name='read', arguments={'path': 'b.txt'}),
            ]),
            _make_text_response('Read both files'),
        ]

        mock_tools_gateway = _make_tools_gateway_mock()
        mock_tools_gateway.execute_tool.side_effect = [
            ToolExecutionResult(name='read', ok=True, content='alpha'),
            ToolExecutionResult(name='read', ok=True, content='beta'),
        ]

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=mock_tools_gateway,
            system_prompt='You are helpful.',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
        )

        result = gateway.run('Read both files')

        self.assertEqual(result.tool_calls, 2)
        self.assertEqual(mock_tools_gateway.execute_tool.call_count, 2)

    def test_tool_execution_error_is_wrapped(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        mock_client.complete.side_effect = [
            _make_tool_call_response([
                ToolCall(id='c1', name='crash_tool', arguments={})
            ]),
            _make_text_response('Handled the error'),
        ]

        mock_tools_gateway = _make_tools_gateway_mock()
        mock_tools_gateway.execute_tool.side_effect = RuntimeError('boom')

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=mock_tools_gateway,
            system_prompt='You are helpful.',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
        )

        result = gateway.run('Trigger crash')

        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.final_output, 'Handled the error')
        # Verify the model received the error in the tool result
        second_call_messages = mock_client.complete.call_args_list[1].kwargs['messages']
        tool_message = [m for m in second_call_messages if m['role'] == 'tool'][0]
        self.assertIn('boom', tool_message['content'])


class AgentGatewayMaxTurnsTests(unittest.TestCase):
    """验证最大轮数限制。"""

    def test_stops_when_max_tool_turns_reached(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        # Always return tool calls so the loop hits the cap
        mock_client.complete.return_value = _make_tool_call_response([
            ToolCall(id='c1', name='noop', arguments={})
        ])

        mock_tools_gateway = _make_tools_gateway_mock()
        mock_tools_gateway.execute_tool.return_value = ToolExecutionResult(
            name='noop', ok=True, content='done',
        )

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=mock_tools_gateway,
            system_prompt='You are helpful.',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
            max_tool_turns=3,
        )

        result = gateway.run('Loop forever')

        self.assertEqual(result.tool_calls, 3)
        self.assertIn('max_tool_turns_reached', result.stop_reason or '')


class AgentGatewayResumeTests(unittest.TestCase):
    """验证从快照恢复会话。"""

    def test_resume_appends_user_message_and_continues(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        mock_client.complete.return_value = _make_text_response('Resumed reply')

        snapshot = AgentSessionSnapshot(
            session_id='snap-001',
            model_config=ModelConfig(model='test-model'),
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            context_policy=__import__('core_contracts.config', fromlist=['ContextPolicy']).ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            budget_config=BudgetConfig(),
            session_paths=SessionPaths(session_directory=Path('/tmp')),
            messages=(
                {'role': 'system', 'content': 'Previous system prompt'},
                {'role': 'user', 'content': 'First question'},
                {'role': 'assistant', 'content': 'First answer'},
            ),
            transcript=(),
        )

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=_make_tools_gateway_mock(),
            system_prompt='Should not be used for resume',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
        )

        result = gateway.resume('Follow-up question', snapshot)

        self.assertEqual(result.final_output, 'Resumed reply')
        self.assertEqual(result.session_id, 'snap-001')
        # Verify old messages are preserved + new user message appended
        messages = mock_client.complete.call_args.kwargs['messages']
        roles = [m['role'] for m in messages]
        self.assertEqual(roles, ['system', 'user', 'assistant', 'user'])
        self.assertEqual(messages[-1]['content'], 'Follow-up question')


class AgentGatewayErrorTests(unittest.TestCase):
    """验证模型调用异常的优雅处理。"""

    def test_model_error_returns_result_with_stop_reason(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        mock_client.complete.side_effect = ConnectionError('API unreachable')

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=_make_tools_gateway_mock(),
            system_prompt='You are helpful.',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
        )

        result = gateway.run('Hello')

        self.assertIn('model_error', result.stop_reason or '')
        self.assertIn('API unreachable', result.stop_reason or '')
        self.assertEqual(result.final_output, '')

    def test_cost_is_computed_when_pricing_available(self) -> None:
        mock_client = create_autospec(ModelClient, instance=True)
        mock_client.complete.return_value = _make_text_response('ok')

        pricing = ModelPricing(
            input_cost_per_million_tokens_usd=15.0,
            output_cost_per_million_tokens_usd=60.0,
        )
        model_config = ModelConfig(model='gpt-4', pricing=pricing)

        gateway = AgentGateway(
            client=mock_client,
            tools_gateway=_make_tools_gateway_mock(),
            system_prompt='You are helpful.',
            workspace_scope=_make_workspace_scope(),
            execution_policy=ExecutionPolicy(),
            permissions=ToolPermissionPolicy(),
            model_config=model_config,
        )

        result = gateway.run('Hi')

        # 10 input + 5 output tokens with $15/$60 per million
        expected_cost = (10 / 1_000_000 * 15.0) + (5 / 1_000_000 * 60.0)
        self.assertAlmostEqual(result.total_cost_usd, expected_cost)


if __name__ == '__main__':
    unittest.main()
