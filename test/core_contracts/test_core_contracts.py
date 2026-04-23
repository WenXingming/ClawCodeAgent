"""ISSUE-001 契约模型的单元测试。

这个测试文件同时承担两类目的：
1) 展示每个契约对象应该如何工作。
2) 验证 from_dict 对异常/不完整输入的安全容错。
3) 保护常见历史字段名的兼容性。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from core_contracts.config import AgentPermissions, AgentRuntimeConfig, BudgetConfig, ModelConfig
from core_contracts.protocol import OneTurnResponse, StreamEvent, ToolCall, ToolExecutionResult
from core_contracts.result import AgentRunResult
from core_contracts.usage import ModelPricing, TokenUsage


_TEST_TMP_ROOT = (Path(__file__).resolve().parent / '.tmp').resolve()


def _make_test_dir() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = _TEST_TMP_ROOT / f'case-{uuid4().hex}'
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


# ---------------------------------------------------------------------------
# 使用统计与 token 计数
# ---------------------------------------------------------------------------


class TokenUsageTests(unittest.TestCase):
    """验证 token 统计行为。"""

    def test_total_tokens_and_add(self) -> None:
        left = TokenUsage(input_tokens=10, output_tokens=5, reasoning_tokens=2)
        right = TokenUsage(input_tokens=3, output_tokens=4)
        total = left + right

        self.assertEqual(total.input_tokens, 13)
        self.assertEqual(total.output_tokens, 9)
        self.assertEqual(total.reasoning_tokens, 2)
        self.assertEqual(total.total_tokens, 22)

    def test_from_dict_supports_legacy_usage_keys(self) -> None:
        usage = TokenUsage.from_dict(
            {
                'prompt_tokens': 12,
                'completion_tokens': 7,
                'reasoningTokens': 3,
            }
        )
        self.assertEqual(usage.input_tokens, 12)
        self.assertEqual(usage.output_tokens, 7)
        self.assertEqual(usage.reasoning_tokens, 3)


class ModelConfigTests(unittest.TestCase):
    """验证模型配置解析与默认值行为。"""

    def test_model_config_round_trip(self) -> None:
        config = ModelConfig(
            model='demo-model',
            base_url='http://127.0.0.1:8001/v1',
            api_key='secret',
            temperature=0.2,
            timeout_seconds=90.0,
            pricing=ModelPricing(input_cost_per_million_tokens_usd=0.5),
        )

        restored = ModelConfig.from_dict(config.to_dict())
        self.assertEqual(restored, config)

    def test_model_config_from_invalid_payload_uses_defaults(self) -> None:
        restored = ModelConfig.from_dict({'temperature': 'bad'})
        self.assertEqual(restored.model, 'unknown-model')
        self.assertEqual(restored.temperature, 0.0)
        self.assertEqual(restored.timeout_seconds, 120.0)


class ModelPricingTests(unittest.TestCase):
    """验证成本估算公式是否符合预期。"""

    def test_estimate_cost_usd(self) -> None:
        pricing = ModelPricing(
            input_cost_per_million_tokens_usd=1.0,
            output_cost_per_million_tokens_usd=2.0,
            cache_creation_input_cost_per_million_tokens_usd=0.5,
            cache_read_input_cost_per_million_tokens_usd=0.25,
        )
        usage = TokenUsage(
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_creation_input_tokens=200_000,
            cache_read_input_tokens=100_000,
        )

        # 1.0 + 1.0 + 0.1 + 0.025
        self.assertAlmostEqual(pricing.estimate_cost_usd(usage), 2.125)


class BudgetConfigTests(unittest.TestCase):
    """验证预算字段及兼容字段名解析。"""

    def test_budget_config_supports_camel_case_keys(self) -> None:
        budget = BudgetConfig.from_dict(
            {
                'maxTotalTokens': 1000,
                'maxToolCalls': '8',
                'maxTotalCostUsd': '2.5',
            }
        )
        self.assertEqual(budget.max_total_tokens, 1000)
        self.assertEqual(budget.max_tool_calls, 8)
        self.assertEqual(budget.max_total_cost_usd, 2.5)

    def test_budget_config_ignores_boolean_numbers(self) -> None:
        budget = BudgetConfig.from_dict({'max_total_tokens': True})
        self.assertIsNone(budget.max_total_tokens)


class RuntimeConfigTests(unittest.TestCase):
    """验证运行配置的默认行为与序列化往返。"""

    def test_runtime_config_defaults_when_fields_missing(self) -> None:
        workspace = _make_test_dir()
        runtime = AgentRuntimeConfig.from_dict({'cwd': str(workspace)})

        self.assertEqual(runtime.max_turns, 12)
        self.assertEqual(runtime.permissions, AgentPermissions())
        self.assertEqual(runtime.budget_config, BudgetConfig())
        self.assertEqual(runtime.cwd, workspace.resolve())
        self.assertTrue(str(runtime.session_directory).endswith('.port_sessions\\agent'))

    def test_runtime_config_round_trip(self) -> None:
        workspace = _make_test_dir()
        runtime = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            permissions=AgentPermissions(allow_file_write=True, allow_shell_commands=True),
            additional_working_directories=(workspace / 'sub',),
            budget_config=BudgetConfig(max_model_calls=3),
        )

        restored = AgentRuntimeConfig.from_dict(runtime.to_dict())

        self.assertEqual(restored.cwd, runtime.cwd.resolve())
        self.assertEqual(restored.max_turns, 5)
        self.assertEqual(restored.permissions.allow_file_write, True)
        self.assertEqual(restored.permissions.allow_shell_commands, True)
        self.assertEqual(restored.budget_config.max_model_calls, 3)
        self.assertEqual(len(restored.additional_working_directories), 1)


class ToolContractsTests(unittest.TestCase):
    """验证工具调用/结果契约的安全行为。"""

    def test_tool_call_invalid_arguments_fall_back_to_empty_dict(self) -> None:
        call = ToolCall.from_dict({'id': '1', 'name': 'read_file', 'arguments': ['bad']})
        self.assertEqual(call.arguments, {})

    def test_tool_result_round_trip(self) -> None:
        result = ToolExecutionResult(
            name='read_file',
            ok=True,
            content='hello',
            metadata={'path': 'README.md'},
        )
        restored = ToolExecutionResult.from_dict(result.to_dict())
        self.assertEqual(restored, result)


class OneTurnResponseTests(unittest.TestCase):
    """验证模型单轮响应契约。"""

    def test_one_turn_response_round_trip(self) -> None:
        turn = OneTurnResponse(
            content='done',
            tool_calls=(
                ToolCall(id='call_1', name='read_file', arguments={'path': 'README.md'}),
            ),
            finish_reason='tool_calls',
            usage=TokenUsage(input_tokens=21, output_tokens=9),
        )

        restored = OneTurnResponse.from_dict(turn.to_dict())
        self.assertEqual(restored.content, 'done')
        self.assertEqual(len(restored.tool_calls), 1)
        self.assertEqual(restored.tool_calls[0].name, 'read_file')
        self.assertEqual(restored.finish_reason, 'tool_calls')
        self.assertEqual(restored.usage.input_tokens, 21)

    def test_one_turn_response_handles_invalid_payload(self) -> None:
        restored = OneTurnResponse.from_dict(
            {
                'content': None,
                'toolCalls': 'bad',
                'finishReason': 123,
                'usage': 'bad',
            }
        )
        self.assertEqual(restored.content, '')
        self.assertEqual(restored.tool_calls, ())
        self.assertEqual(restored.finish_reason, '123')
        self.assertEqual(restored.usage, TokenUsage())


class StreamEventTests(unittest.TestCase):
    """验证流式事件契约。"""

    def test_stream_event_round_trip(self) -> None:
        event = StreamEvent(
            type='tool_call_delta',
            tool_call_index=0,
            tool_call_id='call_1',
            tool_name='read_file',
            arguments_delta='{"path":"README.md"}',
            usage=TokenUsage(input_tokens=2, output_tokens=1),
            raw_event={'source': 'unit_test'},
        )

        restored = StreamEvent.from_dict(event.to_dict())
        self.assertEqual(restored.type, 'tool_call_delta')
        self.assertEqual(restored.tool_call_id, 'call_1')
        self.assertEqual(restored.tool_name, 'read_file')
        self.assertEqual(restored.arguments_delta, '{"path":"README.md"}')
        self.assertEqual(restored.usage.input_tokens, 2)
        self.assertEqual(restored.raw_event['source'], 'unit_test')

    def test_stream_event_handles_invalid_payload(self) -> None:
        restored = StreamEvent.from_dict(
            {
                'type': None,
                'toolCallIndex': 'bad',
                'toolCallId': 123,
                'usage': 'bad',
                'rawEvent': 'bad',
            }
        )
        self.assertEqual(restored.type, 'unknown')
        self.assertIsNone(restored.tool_call_index)
        self.assertEqual(restored.tool_call_id, '123')
        self.assertEqual(restored.usage, TokenUsage())
        self.assertEqual(restored.raw_event, {})


class AgentRunResultTests(unittest.TestCase):
    """验证最终运行结果契约的解析与序列化。"""

    def test_agent_run_result_round_trip(self) -> None:
        result = AgentRunResult(
            final_output='done',
            turns=2,
            tool_calls=1,
            transcript=({'role': 'assistant', 'content': 'done'},),
            usage=TokenUsage(input_tokens=30, output_tokens=12),
            total_cost_usd=0.012,
            stop_reason='completed',
            file_history=({'action': 'write_file', 'path': 'a.py'},),
            session_id='abc',
            session_path='sessions/abc.json',
        )

        restored = AgentRunResult.from_dict(result.to_dict())
        self.assertEqual(restored.final_output, 'done')
        self.assertEqual(restored.turns, 2)
        self.assertEqual(restored.tool_calls, 1)
        self.assertEqual(restored.usage.input_tokens, 30)
        self.assertEqual(restored.usage.output_tokens, 12)
        self.assertEqual(restored.stop_reason, 'completed')
        self.assertEqual(restored.session_id, 'abc')

    def test_agent_run_result_from_invalid_payload_is_safe(self) -> None:
        restored = AgentRunResult.from_dict({'turns': 'x', 'toolCalls': '2'})
        self.assertEqual(restored.turns, 0)
        self.assertEqual(restored.tool_calls, 2)
        self.assertEqual(restored.final_output, '')


if __name__ == '__main__':
    unittest.main()
