"""context 域统一网关。"""

from __future__ import annotations

from dataclasses import dataclass

from context.budget_projection import BudgetProjector
from context.compactor import CompactionResult, Compactor
from context.snipper import Snipper
from core_contracts.budget import BudgetConfig
from core_contracts.context_contracts import BudgetProjection, ContextRunState, PreModelBudgetGuard
from core_contracts.openai_contracts import ModelClient
from core_contracts.protocol import JSONDict, OneTurnResponse
from core_contracts.runtime_policy import ContextPolicy
from core_contracts.token_usage import TokenUsage


_MAX_REACTIVE_COMPACT_RETRIES = 2


@dataclass(frozen=True)
class PreModelContextOutcome:
    """表示一次 pre-model 上下文治理后的结果快照。"""

    pre_model_stop: str | None
    events: tuple[JSONDict, ...]


@dataclass(frozen=True)
class ReactiveCompactOutcome:
    """表示一次模型调用及 reactive compact 重试链路的结果。"""

    response: OneTurnResponse | None
    stop_reason: str | None
    events: tuple[JSONDict, ...]


class ContextGateway:
    """对外暴露 context 能力并屏蔽内部实现细节。"""

    def __init__(
        self,
        client: ModelClient | None = None,
        *,
        budget_projector: BudgetProjector | None = None,
        snipper: Snipper | None = None,
        compactor: Compactor | None = None,
    ) -> None:
        self._budget_projector = budget_projector or BudgetProjector()
        self._snipper = snipper or Snipper()
        if compactor is not None:
            self._compactor = compactor
        elif client is not None:
            self._compactor = Compactor(client)
        else:
            self._compactor = None

    def project_budget(
        self,
        messages: list[JSONDict],
        *,
        tools: list[JSONDict] | None = None,
        max_input_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        soft_buffer_tokens: int | None = None,
    ) -> BudgetProjection:
        """对当前消息和工具集合执行预算投影。"""
        return self._budget_projector.project(
            messages,
            tools=tools,
            max_input_tokens=max_input_tokens,
            output_reserve_tokens=output_reserve_tokens,
            soft_buffer_tokens=soft_buffer_tokens,
        )

    def run_pre_model_cycle(
        self,
        *,
        run_state: ContextRunState,
        budget_config: BudgetConfig,
        context_policy: ContextPolicy,
        guard: PreModelBudgetGuard,
        openai_tools: list[JSONDict],
    ) -> PreModelContextOutcome:
        """执行模型调用前的上下文治理编排并返回统一结果。"""
        events: list[JSONDict] = []
        turn_index = run_state.turn_index
        next_usage_delta = run_state.usage_delta
        next_model_call_count = run_state.model_call_count
        session_state = run_state.session_state
        compactor = self._require_compactor()

        snapshot = self.project_budget(
            session_state.to_messages(),
            tools=openai_tools,
            max_input_tokens=budget_config.max_input_tokens,
        )

        if snapshot.is_soft_over:
            snip_result = self._snipper.snip(
                session_state.messages,
                preserve_messages=context_policy.compact_preserve_messages,
                tools=openai_tools,
                max_input_tokens=budget_config.max_input_tokens,
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
                snapshot = self.project_budget(
                    session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=budget_config.max_input_tokens,
                )

        pre_model_stop = guard.check_pre_model(
            turns_offset=run_state.turns_offset,
            turns_this_run=run_state.turns_this_run,
            model_call_count=next_model_call_count,
            snapshot=snapshot,
            usage_delta=next_usage_delta,
        )

        if compactor.should_auto_compact(snapshot.projected_input_tokens, context_policy.auto_compact_threshold_tokens) and pre_model_stop is None:
            compact_result = compactor.compact(
                session_state.messages,
                preserve_messages=context_policy.compact_preserve_messages,
            )
            if compact_result.compacted:
                next_model_call_count += 1
                next_usage_delta = next_usage_delta + compact_result.usage
                events.append(self._make_compact_event(turn_index, 'auto', compact_result))
                snapshot = self.project_budget(
                    session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=budget_config.max_input_tokens,
                )
                pre_model_stop = guard.check_pre_model(
                    turns_offset=run_state.turns_offset,
                    turns_this_run=run_state.turns_this_run,
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
                        'preserve_messages': context_policy.compact_preserve_messages,
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

        run_state.token_budget_snapshot = snapshot
        run_state.usage_delta = next_usage_delta
        run_state.model_call_count = next_model_call_count

        return PreModelContextOutcome(
            pre_model_stop=pre_model_stop,
            events=tuple(events),
        )

    def complete_with_reactive_compact(
        self,
        *,
        run_state: ContextRunState,
        budget_config: BudgetConfig,
        context_policy: ContextPolicy,
        openai_tools: list[JSONDict],
        guard: PreModelBudgetGuard,
    ) -> ReactiveCompactOutcome:
        """执行模型调用，并在需要时做 reactive compact 重试。"""
        events: list[JSONDict] = []
        session_state = run_state.session_state
        turn_index = run_state.turn_index
        current_usage = run_state.usage_delta
        current_model_call_count = run_state.model_call_count
        attempt = 0
        current_error: Exception | None = None
        compactor = self._require_compactor()

        while True:
            try:
                response = compactor.client.complete(
                    messages=session_state.to_messages(),
                    tools=openai_tools,
                    output_schema=context_policy.output_schema,
                )
                current_model_call_count += 1
                current_usage = current_usage + response.usage
                run_state.usage_delta = current_usage
                run_state.model_call_count = current_model_call_count
                return ReactiveCompactOutcome(
                    response=response,
                    stop_reason=None,
                    events=tuple(events),
                )
            except Exception as exc:
                current_error = exc
                if not compactor.is_context_length_error(exc) or attempt >= _MAX_REACTIVE_COMPACT_RETRIES:
                    break

                attempt += 1
                preserve_messages = max(1, context_policy.compact_preserve_messages - (attempt - 1))
                compact_result = compactor.compact(
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

                snapshot = self.project_budget(
                    session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=budget_config.max_input_tokens,
                )
                run_state.token_budget_snapshot = snapshot
                if stop := guard.check_pre_model(
                    turns_offset=run_state.turns_offset,
                    turns_this_run=run_state.turns_this_run,
                    model_call_count=current_model_call_count,
                    snapshot=snapshot,
                    usage_delta=current_usage,
                ):
                    run_state.usage_delta = current_usage
                    run_state.model_call_count = current_model_call_count
                    return ReactiveCompactOutcome(
                        response=None,
                        stop_reason=stop,
                        events=tuple(events),
                    )

        events.append({'type': 'backend_error', 'turn': turn_index, 'error': str(current_error)})
        run_state.usage_delta = current_usage
        run_state.model_call_count = current_model_call_count
        return ReactiveCompactOutcome(
            response=None,
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

    def _require_compactor(self) -> Compactor:
        """返回 compact 能力；未启用时抛出显式错误。"""
        if self._compactor is None:
            raise RuntimeError('ContextGateway requires a compactor-enabled client for compact operations')
        return self._compactor
