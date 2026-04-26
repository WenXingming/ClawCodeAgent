"""上下文治理编排：统一 pre-model 阶段的 snip/compact/预算预检。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from budget.budget_guard import BudgetGuard
from context.context_budget_evaluator import ContextBudgetEvaluator, ContextBudgetSnapshot
from context.context_compactor import CompactionResult, ContextCompactor
from context.context_snipper import ContextSnipper
from core_contracts.config import AgentRuntimeConfig
from core_contracts.protocol import JSONDict, OneTurnResponse
from core_contracts.usage import TokenUsage
from openai_client.openai_client import OpenAIClient, OpenAIClientError


_MAX_REACTIVE_COMPACT_RETRIES = 2


class _SessionStateLike(Protocol):
    """用于描述 run loop 所需的最小 session_state 协议。"""

    messages: list[JSONDict]

    def to_messages(self) -> list[JSONDict]:
        ...


@dataclass(frozen=True)
class PreModelContextOutcome:
    """描述一次 pre-model 上下文治理后的状态。"""

    snapshot: ContextBudgetSnapshot
    usage_delta: TokenUsage
    model_call_count: int
    pre_model_stop: str | None
    events: tuple[JSONDict, ...]


@dataclass(frozen=True)
class ReactiveCompactOutcome:
    """描述一次模型调用（含 reactive compact 重试）后的状态。"""

    response: OneTurnResponse | None
    usage_delta: TokenUsage
    model_call_count: int
    stop_reason: str | None
    events: tuple[JSONDict, ...]


@dataclass
class BudgetContextOrchestrator:
    """组合 snipper 与 compactor，统一处理上下文治理与预算编排。"""

    budget_evaluator: ContextBudgetEvaluator
    context_snipper: ContextSnipper
    context_compactor: ContextCompactor

    def run_pre_model_cycle(
        self,
        *,
        session_state: _SessionStateLike,
        runtime_config: AgentRuntimeConfig,
        guard: BudgetGuard,
        openai_tools: list[JSONDict],
        turn_index: int,
        turns_offset: int,
        turns_this_run: int,
        usage_delta: TokenUsage,
        model_call_count: int,
    ) -> PreModelContextOutcome:
        """执行模型调用前的上下文治理编排并返回统一结果。"""
        events: list[JSONDict] = []
        next_usage_delta = usage_delta
        next_model_call_count = model_call_count

        snapshot = self.budget_evaluator.evaluate(
            messages=session_state.to_messages(),
            tools=openai_tools,
            max_input_tokens=runtime_config.budget_config.max_input_tokens,
        )

        if snapshot.is_soft_over:
            snip_result = self.context_snipper.snip(
                session_state.messages,
                preserve_messages=runtime_config.compact_preserve_messages,
                tools=openai_tools,
                max_input_tokens=runtime_config.budget_config.max_input_tokens,
            )
            if snip_result.snipped_count > 0:
                events.append(
                    {
                        'type': 'snip_boundary',
                        'turn': turn_index,
                        'snipped_count': snip_result.snipped_count,
                        'tokens_removed': snip_result.tokens_removed,
                    }
                )
                snapshot = self.budget_evaluator.evaluate(
                    messages=session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=runtime_config.budget_config.max_input_tokens,
                )

        pre_model_stop = guard.check_pre_model(
            turns_offset=turns_offset,
            turns_this_run=turns_this_run,
            model_call_count=next_model_call_count,
            snapshot=snapshot,
            usage_delta=next_usage_delta,
        )

        if (
            self.context_compactor.should_auto_compact(
                snapshot.projected_input_tokens,
                runtime_config.auto_compact_threshold_tokens,
            )
            and pre_model_stop is None
        ):
            compact_result = self.context_compactor.compact(
                session_state.messages,
                preserve_messages=runtime_config.compact_preserve_messages,
            )
            if compact_result.compacted:
                next_model_call_count += 1
                next_usage_delta = next_usage_delta + compact_result.usage
                events.append(self._make_compact_event(turn_index, 'auto', compact_result))
                snapshot = self.budget_evaluator.evaluate(
                    messages=session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=runtime_config.budget_config.max_input_tokens,
                )
                pre_model_stop = guard.check_pre_model(
                    turns_offset=turns_offset,
                    turns_this_run=turns_this_run,
                    model_call_count=next_model_call_count,
                    snapshot=snapshot,
                    usage_delta=next_usage_delta,
                )
            elif compact_result.error:
                events.append(
                    {
                        'type': 'compact_failed',
                        'turn': turn_index,
                        'trigger': 'auto',
                        'error': compact_result.error,
                        'preserve_messages': runtime_config.compact_preserve_messages,
                    }
                )

        events.append(
            {
                'type': 'token_budget',
                'turn': turn_index,
                'projected': snapshot.projected_input_tokens,
                'is_hard_over': snapshot.is_hard_over,
                'is_soft_over': snapshot.is_soft_over,
            }
        )

        return PreModelContextOutcome(
            snapshot=snapshot,
            usage_delta=next_usage_delta,
            model_call_count=next_model_call_count,
            pre_model_stop=pre_model_stop,
            events=tuple(events),
        )

    def complete_with_reactive_compact(
        self,
        *,
        client: OpenAIClient,
        session_state: _SessionStateLike,
        runtime_config: AgentRuntimeConfig,
        openai_tools: list[JSONDict],
        turn_index: int,
        guard: BudgetGuard,
        turns_offset: int,
        turns_this_run: int,
        usage_delta: TokenUsage,
        model_call_count: int,
    ) -> ReactiveCompactOutcome:
        """执行模型调用；必要时在 context-length 错误后进行 reactive compact 重试。"""
        events: list[JSONDict] = []
        current_usage = usage_delta
        current_model_call_count = model_call_count
        attempt = 0
        current_error: OpenAIClientError | None = None

        while True:
            try:
                response = client.complete(
                    messages=session_state.to_messages(),
                    tools=openai_tools,
                    output_schema=runtime_config.output_schema,
                )
                current_model_call_count += 1
                current_usage = current_usage + response.usage
                return ReactiveCompactOutcome(
                    response=response,
                    usage_delta=current_usage,
                    model_call_count=current_model_call_count,
                    stop_reason=None,
                    events=tuple(events),
                )
            except OpenAIClientError as exc:
                current_error = exc
                if not self.context_compactor.is_context_length_error(exc) or attempt >= _MAX_REACTIVE_COMPACT_RETRIES:
                    break

                attempt += 1
                preserve_messages = max(
                    1,
                    runtime_config.compact_preserve_messages - (attempt - 1),
                )
                compact_result = self.context_compactor.compact(
                    session_state.messages,
                    preserve_messages=preserve_messages,
                )

                retry_event: JSONDict = {
                    'type': 'reactive_compact_retry',
                    'turn': turn_index,
                    'attempt': attempt,
                    'preserve_messages': preserve_messages,
                    'context_error': str(exc),
                }

                if not compact_result.compacted:
                    retry_event['ok'] = False
                    retry_event['error'] = compact_result.error or 'Reactive compact made no progress'
                    events.append(retry_event)
                    break

                current_model_call_count += 1
                current_usage = current_usage + compact_result.usage
                retry_event['ok'] = True
                retry_event['tokens_removed'] = compact_result.tokens_removed
                retry_event['messages_replaced'] = compact_result.messages_replaced
                events.append(self._make_compact_event(turn_index, 'reactive', compact_result, attempt=attempt))
                events.append(retry_event)

                snapshot = self.budget_evaluator.evaluate(
                    messages=session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=runtime_config.budget_config.max_input_tokens,
                )
                if stop := guard.check_pre_model(
                    turns_offset=turns_offset,
                    turns_this_run=turns_this_run,
                    model_call_count=current_model_call_count,
                    snapshot=snapshot,
                    usage_delta=current_usage,
                ):
                    return ReactiveCompactOutcome(
                        response=None,
                        usage_delta=current_usage,
                        model_call_count=current_model_call_count,
                        stop_reason=stop,
                        events=tuple(events),
                    )

        events.append({'type': 'backend_error', 'turn': turn_index, 'error': str(current_error)})
        return ReactiveCompactOutcome(
            response=None,
            usage_delta=current_usage,
            model_call_count=current_model_call_count,
            stop_reason=None,
            events=tuple(events),
        )

    @staticmethod
    def _make_compact_event(
        turn_index: int,
        trigger: str,
        result: CompactionResult,
        *,
        attempt: int | None = None,
    ) -> JSONDict:
        """统一构造 compact_boundary 事件。"""
        event: JSONDict = {
            'type': 'compact_boundary',
            'turn': turn_index,
            'trigger': trigger,
            'messages_replaced': result.messages_replaced,
            'tokens_removed': result.tokens_removed,
            'pre_tokens': result.pre_tokens,
            'post_tokens': result.post_tokens,
            'preserve_messages': result.preserve_messages_used,
        }
        if attempt is not None:
            event['attempt'] = attempt
        return event
