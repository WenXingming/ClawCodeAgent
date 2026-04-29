"""ContextGateway 单元测试。"""

from __future__ import annotations

from dataclasses import dataclass
import unittest
from pathlib import Path
from uuid import uuid4

from agent.run_state import AgentRunState
from agent.run_limits import RunLimits
from context.context_gateway import ContextGateway
from core_contracts.config import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.messaging import OneTurnResponse, ToolCall
from core_contracts.config import ContextPolicy
from core_contracts.primitives import TokenUsage
from openai_client.openai_client import OpenAIClient
from core_contracts.session import AgentSessionState


@dataclass(frozen=True)
class _RuntimePolicies:
    budget_config: BudgetConfig
    context_policy: ContextPolicy


_TEST_TMP_ROOT = (Path(__file__).resolve().parent / '.tmp').resolve()


def _make_test_dir() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = _TEST_TMP_ROOT / f'case-{uuid4().hex}'
    workspace.mkdir(parents=True, exist_ok=False)
    return workspace


class _FakeOpenAIClient(OpenAIClient):
    """用预置响应替代真实网络调用。"""

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

    def complete(self, messages, tools=None, *, output_schema=None):  # type: ignore[override]
        if not self._responses:
            raise AssertionError('No prepared response left for test')
        current = self._responses.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class ContextGatewayTests(unittest.TestCase):

    def _build_runtime_policies(self, *, budget: BudgetConfig | None = None, context_policy: ContextPolicy | None = None) -> _RuntimePolicies:
        return _RuntimePolicies(
            budget_config=budget or BudgetConfig(),
            context_policy=context_policy or ContextPolicy(compact_preserve_messages=1),
        )

    def test_run_pre_model_cycle_snips_when_soft_over(self) -> None:
        _make_test_dir()
        runtime_policies = self._build_runtime_policies(
            budget=BudgetConfig(max_input_tokens=5000),
        )

        session_state = AgentSessionState()
        session_state.append_user('任务')
        session_state.append_assistant_turn(
            OneTurnResponse(
                content='',
                tool_calls=(ToolCall(id='call_1', name='read_file', arguments={'path': 'demo.txt'}),),
                finish_reason='tool_calls',
                usage=TokenUsage(),
            )
        )
        session_state.append_user('继续')
        run_state = AgentRunState.for_new_session(
            session_state=session_state,
            session_id='session-001',
        )
        run_state.begin_turn(1)

        context_manager = ContextGateway(client=_FakeOpenAIClient([]))
        guard = RunLimits(
            budget=runtime_policies.budget_config,
            pricing=ModelConfig(model='fake').pricing,
            cost_baseline=0.0,
        )

        outcome = context_manager.run_pre_model_cycle(
            run_state=run_state,
            budget_config=runtime_policies.budget_config,
            context_policy=runtime_policies.context_policy,
            guard=guard,
            openai_tools=[],
        )

        event_types = [item.get('type') for item in outcome.events]
        self.assertIn('snip_boundary', event_types)
        self.assertIn('token_budget', event_types)
        self.assertIsNone(outcome.pre_model_stop)
        self.assertIsNotNone(run_state.token_budget_snapshot)

    def test_run_pre_model_cycle_auto_compact_updates_usage_and_count(self) -> None:
        _make_test_dir()
        runtime_policies = self._build_runtime_policies(
            context_policy=ContextPolicy(compact_preserve_messages=1, auto_compact_threshold_tokens=1),
        )

        session_state = AgentSessionState()
        session_state.append_user('旧需求 ' * 80)
        session_state.append_assistant_turn(
            OneTurnResponse(content='旧回答 ' * 80, tool_calls=(), finish_reason='stop', usage=TokenUsage())
        )
        session_state.append_user('继续执行')
        run_state = AgentRunState.for_new_session(
            session_state=session_state,
            session_id='session-001',
        )
        run_state.begin_turn(1)

        compact_usage = TokenUsage(input_tokens=2, output_tokens=1)
        context_manager = ContextGateway(
            client=_FakeOpenAIClient([
                OneTurnResponse(
                    content='用户目标：继续任务\n下一步：回复最新请求',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=compact_usage,
                )
            ])
        )
        guard = RunLimits(
            budget=runtime_policies.budget_config,
            pricing=ModelConfig(model='fake').pricing,
            cost_baseline=0.0,
        )

        outcome = context_manager.run_pre_model_cycle(
            run_state=run_state,
            budget_config=runtime_policies.budget_config,
            context_policy=runtime_policies.context_policy,
            guard=guard,
            openai_tools=[],
        )

        compact_events = [item for item in outcome.events if item.get('type') == 'compact_boundary']
        self.assertGreater(len(compact_events), 0)
        self.assertEqual(run_state.model_call_count, 1)
        self.assertEqual(run_state.usage_delta.input_tokens, compact_usage.input_tokens)
        self.assertEqual(run_state.usage_delta.output_tokens, compact_usage.output_tokens)
        self.assertIsNone(outcome.pre_model_stop)


if __name__ == '__main__':
    unittest.main()
