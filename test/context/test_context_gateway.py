"""ContextGateway 集成级单元测试。

覆盖三条公开路径：
1. project_budget()               —— 无客户端即可工作的预算投影。
2. run_pre_model_cycle()          —— snip → guard → auto-compact 完整链路。
3. complete_with_reactive_compact() —— 模型调用与 reactive compact 重试链路。

测试策略：
- 通过 unittest.mock.MagicMock / 轻量级 Stub 替换全部外部依赖（ModelClient、
  PreModelBudgetGuard、ContextRunState），确保每个用例只测试网关自身的编排逻辑。
- 绝不调用真实 LLM；所有 client.complete() 均为预置响应序列。
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, call, patch

from context import ContextGateway, create_context_gateway
from core_contracts.config import BudgetConfig, ContextPolicy
from core_contracts.context_contracts import (
    BudgetProjection,
    CompactionResult,
    ContextRunState,
    PreModelBudgetGuard,
    PreModelContextOutcome,
    ReactiveCompactOutcome,
    SnipResult,
)
from core_contracts.messaging import OneTurnResponse
from core_contracts.primitives import TokenUsage


# ---------------------------------------------------------------------------
# 测试用 Stub / Fake 构造辅助
# ---------------------------------------------------------------------------


def _make_response(content: str = 'ok') -> OneTurnResponse:
    """构造一个最简的模型响应对象。"""
    return OneTurnResponse(
        content=content,
        finish_reason='stop',
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        tool_calls=[],
    )


class _FakeSessionState:
    """SessionMessageView 协议的最小 Stub 实现。"""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages: list[dict[str, Any]] = messages or [
            {'role': 'user', 'content': 'hello'},
        ]

    def to_messages(self) -> list[dict[str, Any]]:
        """返回当前消息列表的拷贝。"""
        return list(self.messages)


class _FakeRunState:
    """ContextRunState 协议的可变 Stub 实现。

    允许测试后断言 usage_delta / model_call_count 等字段是否被正确更新。
    """

    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        turn_index: int = 0,
        turns_offset: int = 0,
        turns_this_run: int = 0,
        model_call_count: int = 0,
    ) -> None:
        self.session_state = _FakeSessionState(messages)
        self.turn_index: int = turn_index
        self.usage_delta: TokenUsage = TokenUsage()
        self.model_call_count: int = model_call_count
        self.turns_offset: int = turns_offset
        self.turns_this_run: int = turns_this_run
        self.token_budget_snapshot: BudgetProjection | None = None


def _allow_guard() -> PreModelBudgetGuard:
    """构造一个永远允许继续的预算守卫 Mock。"""
    guard = MagicMock(spec=PreModelBudgetGuard)
    guard.check_pre_model.return_value = None  # None = 允许继续
    return guard


def _deny_guard(reason: str = 'token_limit') -> PreModelBudgetGuard:
    """构造一个永远拒绝继续的预算守卫 Mock。"""
    guard = MagicMock(spec=PreModelBudgetGuard)
    guard.check_pre_model.return_value = reason
    return guard


def _make_client(responses: list[OneTurnResponse | Exception] | None = None) -> MagicMock:
    """构造一个按预置响应序列回答的模型客户端 Mock。"""
    client = MagicMock()
    seq = list(responses or [_make_response()])

    def _complete(**kwargs: Any) -> OneTurnResponse:
        if not seq:
            raise AssertionError('No prepared response left for test client')
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client.complete.side_effect = _complete
    return client


def _default_budget_config() -> BudgetConfig:
    return BudgetConfig(max_input_tokens=200_000)


def _default_context_policy() -> ContextPolicy:
    return ContextPolicy(compact_preserve_messages=4)


# ---------------------------------------------------------------------------
# 1. project_budget 测试
# ---------------------------------------------------------------------------


class ProjectBudgetTests(unittest.TestCase):
    """ContextGateway.project_budget() 的行为测试。"""

    def setUp(self) -> None:
        self.gw = create_context_gateway()  # 无客户端也可投影预算

    def test_returns_budget_projection_dataclass(self) -> None:
        """project_budget 应返回 BudgetProjection 实例。"""
        result = self.gw.project_budget([{'role': 'user', 'content': 'hi'}])
        self.assertIsInstance(result, BudgetProjection)

    def test_no_limit_never_over(self) -> None:
        """未设置 max_input_tokens 时，is_hard_over 与 is_soft_over 均为 False。"""
        result = self.gw.project_budget(
            [{'role': 'user', 'content': 'hello'}],
            max_input_tokens=None,
        )
        self.assertFalse(result.is_hard_over)
        self.assertFalse(result.is_soft_over)
        self.assertIsNone(result.hard_input_limit)

    def test_projected_tokens_positive(self) -> None:
        """有内容的消息应产生正的投影 token 数。"""
        result = self.gw.project_budget([{'role': 'user', 'content': 'hello world'}])
        self.assertGreater(result.projected_input_tokens, 0)

    def test_tools_increase_projection(self) -> None:
        """工具 schema 应增加投影 token 数。"""
        msgs = [{'role': 'user', 'content': 'hi'}]
        no_tools = self.gw.project_budget(msgs)
        with_tools = self.gw.project_budget(
            msgs,
            tools=[{'name': 'read_file', 'description': 'read', 'parameters': {}}],
        )
        self.assertGreater(with_tools.projected_input_tokens, no_tools.projected_input_tokens)

    def test_hard_over_when_limit_tiny(self) -> None:
        """消息超出极小 max_input_tokens 时应触发 is_hard_over。"""
        result = self.gw.project_budget(
            [{'role': 'user', 'content': 'hello world'}],
            max_input_tokens=1,
            output_reserve_tokens=0,
            soft_buffer_tokens=0,
        )
        self.assertTrue(result.is_hard_over)


# ---------------------------------------------------------------------------
# 2. run_pre_model_cycle 测试
# ---------------------------------------------------------------------------


class RunPreModelCycleTests(unittest.TestCase):
    """ContextGateway.run_pre_model_cycle() 的行为测试。"""

    def _make_gateway(self, client: MagicMock | None = None) -> ContextGateway:
        return create_context_gateway(client=client or _make_client())

    def test_returns_pre_model_context_outcome(self) -> None:
        """调用应返回 PreModelContextOutcome 实例。"""
        gw = self._make_gateway()
        run_state = _FakeRunState()
        result = gw.run_pre_model_cycle(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=_default_context_policy(),
            guard=_allow_guard(),
            openai_tools=[],
        )
        self.assertIsInstance(result, PreModelContextOutcome)

    def test_pre_model_stop_propagated_from_guard(self) -> None:
        """守卫拒绝时，pre_model_stop 应携带拒绝原因。"""
        gw = self._make_gateway()
        run_state = _FakeRunState()
        result = gw.run_pre_model_cycle(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=_default_context_policy(),
            guard=_deny_guard('max_tokens_reached'),
            openai_tools=[],
        )
        self.assertEqual(result.pre_model_stop, 'max_tokens_reached')

    def test_token_budget_event_always_emitted(self) -> None:
        """run_pre_model_cycle 必须在 events 中包含 token_budget 事件。"""
        gw = self._make_gateway()
        run_state = _FakeRunState()
        result = gw.run_pre_model_cycle(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=_default_context_policy(),
            guard=_allow_guard(),
            openai_tools=[],
        )
        event_types = [e['type'] for e in result.events]
        self.assertIn('token_budget', event_types)

    def test_auto_compact_triggered_above_threshold(self) -> None:
        """当 projected_tokens >= 阈值时，应触发 auto-compact 并向 events 写入 compact_boundary。"""
        client = _make_client([
            _make_response('Summary text'),  # compact 模型调用
        ])
        gw = create_context_gateway(client=client)

        # 构造足够多的消息确保投影超出极小阈值
        messages = [
            {'role': 'system', 'content': 'you are a helpful assistant'},
            {'role': 'user', 'content': 'old message ' * 50},
            {'role': 'assistant', 'content': 'old reply ' * 50},
            {'role': 'user', 'content': 'latest question'},
        ]
        run_state = _FakeRunState(messages=messages)

        # 设置极低阈值确保触发 auto-compact
        policy = ContextPolicy(
            auto_compact_threshold_tokens=1,
            compact_preserve_messages=1,
        )

        result = gw.run_pre_model_cycle(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=policy,
            guard=_allow_guard(),
            openai_tools=[],
        )
        event_types = [e['type'] for e in result.events]
        self.assertIn('compact_boundary', event_types)

    def test_no_compact_when_below_threshold(self) -> None:
        """投影未达阈值时，不应产生 compact_boundary 事件。"""
        gw = self._make_gateway()
        run_state = _FakeRunState()
        policy = ContextPolicy(auto_compact_threshold_tokens=9_999_999)

        result = gw.run_pre_model_cycle(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=policy,
            guard=_allow_guard(),
            openai_tools=[],
        )
        event_types = [e['type'] for e in result.events]
        self.assertNotIn('compact_boundary', event_types)

    def test_compact_requires_client(self) -> None:
        """无客户端时触发 auto-compact 阈值应抛出 RuntimeError。"""
        gw = create_context_gateway()  # 无客户端
        messages = [
            {'role': 'user', 'content': 'old message ' * 50},
            {'role': 'assistant', 'content': 'old reply ' * 50},
            {'role': 'user', 'content': 'latest'},
        ]
        run_state = _FakeRunState(messages=messages)
        policy = ContextPolicy(auto_compact_threshold_tokens=1, compact_preserve_messages=1)

        with self.assertRaises(RuntimeError):
            gw.run_pre_model_cycle(
                run_state=run_state,
                budget_config=_default_budget_config(),
                context_policy=policy,
                guard=_allow_guard(),
                openai_tools=[],
            )

    def test_run_state_snapshot_updated(self) -> None:
        """run_pre_model_cycle 后，run_state.token_budget_snapshot 应被更新。"""
        gw = self._make_gateway()
        run_state = _FakeRunState()
        self.assertIsNone(run_state.token_budget_snapshot)

        gw.run_pre_model_cycle(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=_default_context_policy(),
            guard=_allow_guard(),
            openai_tools=[],
        )
        self.assertIsNotNone(run_state.token_budget_snapshot)


# ---------------------------------------------------------------------------
# 3. complete_with_reactive_compact 测试
# ---------------------------------------------------------------------------


class CompleteWithReactiveCompactTests(unittest.TestCase):
    """ContextGateway.complete_with_reactive_compact() 的行为测试。"""

    def test_returns_reactive_compact_outcome(self) -> None:
        """成功调用应返回 ReactiveCompactOutcome 实例。"""
        gw = create_context_gateway(client=_make_client([_make_response()]))
        run_state = _FakeRunState()
        result = gw.complete_with_reactive_compact(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=_default_context_policy(),
            openai_tools=[],
            guard=_allow_guard(),
        )
        self.assertIsInstance(result, ReactiveCompactOutcome)

    def test_successful_call_returns_response(self) -> None:
        """模型调用成功时，response 应为 OneTurnResponse，stop_reason 为 None。"""
        expected = _make_response('hello from model')
        gw = create_context_gateway(client=_make_client([expected]))
        run_state = _FakeRunState()

        result = gw.complete_with_reactive_compact(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=_default_context_policy(),
            openai_tools=[],
            guard=_allow_guard(),
        )

        self.assertIsNotNone(result.response)
        self.assertIsNone(result.stop_reason)
        self.assertEqual(result.response.content, 'hello from model')

    def test_usage_delta_accumulated_after_successful_call(self) -> None:
        """成功调用后，run_state.usage_delta 应累计本次调用的 token 用量。"""
        usage = TokenUsage(input_tokens=20, output_tokens=8)
        response = OneTurnResponse(
            content='result', finish_reason='stop', usage=usage, tool_calls=[]
        )
        gw = create_context_gateway(client=_make_client([response]))
        run_state = _FakeRunState()

        gw.complete_with_reactive_compact(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=_default_context_policy(),
            openai_tools=[],
            guard=_allow_guard(),
        )

        self.assertEqual(run_state.usage_delta.input_tokens, 20)
        self.assertEqual(run_state.usage_delta.output_tokens, 8)

    def test_requires_client_raises_when_none(self) -> None:
        """未注入客户端时应抛出 RuntimeError，不应静默失败。"""
        gw = create_context_gateway()  # 无客户端
        run_state = _FakeRunState()

        with self.assertRaises(RuntimeError):
            gw.complete_with_reactive_compact(
                run_state=run_state,
                budget_config=_default_budget_config(),
                context_policy=_default_context_policy(),
                openai_tools=[],
                guard=_allow_guard(),
            )

    def test_non_context_error_is_not_retried(self) -> None:
        """非上下文长度错误不应触发重试，应写入 backend_error 事件并返回。"""
        generic_error = RuntimeError('unexpected server error')
        gw = create_context_gateway(client=_make_client([generic_error]))
        run_state = _FakeRunState()

        result = gw.complete_with_reactive_compact(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=_default_context_policy(),
            openai_tools=[],
            guard=_allow_guard(),
        )

        self.assertIsNone(result.response)
        self.assertIsNone(result.stop_reason)
        event_types = [e['type'] for e in result.events]
        self.assertIn('backend_error', event_types)

    def test_context_length_error_triggers_reactive_compact_and_retries(self) -> None:
        """context length 错误应触发 reactive compact，成功后重试模型调用。"""
        from core_contracts.errors import ModelResponseError

        ctx_error = ModelResponseError(
            'HTTP 400 from model backend: maximum context length exceeded',
            status_code=400,
            detail='maximum context length exceeded',
        )
        success_response = _make_response('recovered')
        # 序列：context_error → compact_call（成功）→ 重试后 model_call（成功）
        compact_summary = OneTurnResponse(
            content='Compact summary', finish_reason='stop', usage=TokenUsage(), tool_calls=[]
        )
        client = _make_client([ctx_error, compact_summary, success_response])
        messages = [
            {'role': 'system', 'content': 'system prompt'},
            {'role': 'user', 'content': 'old question ' * 20},
            {'role': 'assistant', 'content': 'old answer ' * 20},
            {'role': 'user', 'content': 'latest question'},
        ]
        run_state = _FakeRunState(messages=messages)
        policy = ContextPolicy(compact_preserve_messages=1)

        gw = create_context_gateway(client=client)
        result = gw.complete_with_reactive_compact(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=policy,
            openai_tools=[],
            guard=_allow_guard(),
        )

        # 应成功恢复
        self.assertIsNotNone(result.response)
        self.assertEqual(result.response.content, 'recovered')
        event_types = [e['type'] for e in result.events]
        self.assertIn('compact_boundary', event_types)

    def test_guard_stops_after_reactive_compact(self) -> None:
        """reactive compact 后守卫仍拒绝时，应返回 stop_reason 而非继续模型调用。"""
        from core_contracts.errors import ModelResponseError

        ctx_error = ModelResponseError(
            'HTTP 400: maximum context length exceeded',
            status_code=400,
            detail='maximum context length exceeded',
        )
        compact_summary = OneTurnResponse(
            content='Compact summary', finish_reason='stop', usage=TokenUsage(), tool_calls=[]
        )
        # client 只需准备：ctx_error + compact_summary；守卫拒绝后不再调用模型
        client = _make_client([ctx_error, compact_summary])
        messages = [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'old question ' * 20},
            {'role': 'assistant', 'content': 'old answer ' * 20},
            {'role': 'user', 'content': 'latest'},
        ]
        run_state = _FakeRunState(messages=messages)
        policy = ContextPolicy(compact_preserve_messages=1)
        guard = _deny_guard('token_limit_after_compact')

        gw = create_context_gateway(client=client)
        result = gw.complete_with_reactive_compact(
            run_state=run_state,
            budget_config=_default_budget_config(),
            context_policy=policy,
            openai_tools=[],
            guard=guard,
        )

        self.assertIsNone(result.response)
        self.assertEqual(result.stop_reason, 'token_limit_after_compact')


if __name__ == '__main__':
    unittest.main()
