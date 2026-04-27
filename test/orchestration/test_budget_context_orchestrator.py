"""BudgetContextOrchestrator 单元测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from context.context_budget_evaluator import ContextBudgetEvaluator
from budget.budget_guard import BudgetGuard
from context.context_compactor import ContextCompactor
from orchestration.budget_context_orchestrator import BudgetContextOrchestrator
from context.context_snipper import ContextSnipper
from core_contracts.config import AgentPermissions, AgentRuntimeConfig, BudgetConfig, ModelConfig
from core_contracts.protocol import OneTurnResponse, ToolCall
from core_contracts.token_usage import TokenUsage
from openai_client.openai_client import OpenAIClient
from session.session_state import AgentSessionState


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


class BudgetContextOrchestratorTests(unittest.TestCase):

    def _build_runtime_config(self, workspace: Path) -> AgentRuntimeConfig:
        return AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=(workspace / 'sessions'),
            permissions=AgentPermissions(allow_file_write=True),
            compact_preserve_messages=1,
        )

    def test_run_pre_model_cycle_snips_when_soft_over(self) -> None:
        workspace = _make_test_dir()
        runtime_config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=(workspace / 'sessions'),
            permissions=AgentPermissions(allow_file_write=True),
            budget_config=BudgetConfig(max_input_tokens=5000),
            compact_preserve_messages=1,
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

        orchestrator = BudgetContextOrchestrator(
            budget_evaluator=ContextBudgetEvaluator(),
            context_snipper=ContextSnipper(),
            context_compactor=ContextCompactor(_FakeOpenAIClient([])),
        )
        guard = BudgetGuard(
            budget=runtime_config.budget_config,
            pricing=ModelConfig(model='fake').pricing,
            cost_baseline=0.0,
        )

        outcome = orchestrator.run_pre_model_cycle(
            session_state=session_state,
            runtime_config=runtime_config,
            guard=guard,
            openai_tools=[],
            turn_index=1,
            turns_offset=0,
            turns_this_run=1,
            usage_delta=TokenUsage(),
            model_call_count=0,
        )

        event_types = [item.get('type') for item in outcome.events]
        self.assertIn('snip_boundary', event_types)
        self.assertIn('token_budget', event_types)
        self.assertIsNone(outcome.pre_model_stop)

    def test_run_pre_model_cycle_auto_compact_updates_usage_and_count(self) -> None:
        workspace = _make_test_dir()
        runtime_config = AgentRuntimeConfig(
            cwd=workspace,
            max_turns=5,
            session_directory=(workspace / 'sessions'),
            permissions=AgentPermissions(allow_file_write=True),
            compact_preserve_messages=1,
            auto_compact_threshold_tokens=1,
        )

        session_state = AgentSessionState()
        session_state.append_user('旧需求 ' * 80)
        session_state.append_assistant_turn(
            OneTurnResponse(content='旧回答 ' * 80, tool_calls=(), finish_reason='stop', usage=TokenUsage())
        )
        session_state.append_user('继续执行')

        compact_usage = TokenUsage(input_tokens=2, output_tokens=1)
        orchestrator = BudgetContextOrchestrator(
            budget_evaluator=ContextBudgetEvaluator(),
            context_snipper=ContextSnipper(),
            context_compactor=ContextCompactor(
                _FakeOpenAIClient([
                    OneTurnResponse(
                        content='用户目标：继续任务\n下一步：回复最新请求',
                        tool_calls=(),
                        finish_reason='stop',
                        usage=compact_usage,
                    )
                ])
            ),
        )
        guard = BudgetGuard(
            budget=runtime_config.budget_config,
            pricing=ModelConfig(model='fake').pricing,
            cost_baseline=0.0,
        )

        outcome = orchestrator.run_pre_model_cycle(
            session_state=session_state,
            runtime_config=runtime_config,
            guard=guard,
            openai_tools=[],
            turn_index=1,
            turns_offset=0,
            turns_this_run=1,
            usage_delta=TokenUsage(),
            model_call_count=0,
        )

        compact_events = [item for item in outcome.events if item.get('type') == 'compact_boundary']
        self.assertGreater(len(compact_events), 0)
        self.assertEqual(outcome.model_call_count, 1)
        self.assertEqual(outcome.usage_delta.input_tokens, compact_usage.input_tokens)
        self.assertEqual(outcome.usage_delta.output_tokens, compact_usage.output_tokens)


if __name__ == '__main__':
    unittest.main()
