"""统一编排 pre-model 阶段的上下文治理与预算检查。

本模块负责把预算预检、snip、auto compact 和 reactive compact 重试收敛为单一编排层，供 `LocalAgent` 在模型调用前后复用一致的上下文治理逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from budget.budget_guard import BudgetGuard
from context.context_token_budget_evaluator import ContextTokenBudgetEvaluator, ContextTokenBudgetSnapshot
from context.context_compactor import CompactionResult, ContextCompactor
from context.context_snipper import ContextSnipper
from core_contracts.budget import BudgetConfig
from core_contracts.protocol import JSONDict, OneTurnResponse
from core_contracts.runtime_policy import ContextPolicy
from core_contracts.token_usage import TokenUsage
from openai_client.openai_client import OpenAIClient, OpenAIClientError


_MAX_REACTIVE_COMPACT_RETRIES = 2


class _SessionStateLike(Protocol):
    """描述 run loop 所需的最小 session_state 协议。"""

    messages: list[JSONDict]

    def to_messages(self) -> list[JSONDict]:
        ...


@dataclass(frozen=True)
class PreModelContextOutcome:
    """表示一次 pre-model 上下文治理后的结果快照。"""

    snapshot: ContextTokenBudgetSnapshot  # ContextTokenBudgetSnapshot：治理完成后的最新预算快照。
    usage_delta: TokenUsage  # TokenUsage：包含 auto compact 额外消耗后的累计 usage 增量。
    model_call_count: int  # int：包含 compact 调用后更新过的模型调用计数。
    pre_model_stop: str | None  # str | None：pre-model 阶段命中的预算停止原因。
    events: tuple[JSONDict, ...]  # tuple[JSONDict, ...]：本轮 pre-model 阶段产生的结构化事件集合。


@dataclass(frozen=True)
class ReactiveCompactOutcome:
    """表示一次模型调用及 reactive compact 重试链路的结果。"""

    response: OneTurnResponse | None  # OneTurnResponse | None：成功完成模型调用时的最终响应。
    usage_delta: TokenUsage  # TokenUsage：包含 reactive compact 额外消耗后的累计 usage 增量。
    model_call_count: int  # int：包含 reactive compact 调用后的最新模型调用计数。
    stop_reason: str | None  # str | None：reactive compact 之后命中的预算停止原因。
    events: tuple[JSONDict, ...]  # tuple[JSONDict, ...]：本轮调用与重试过程中产生的结构化事件集合。


@dataclass
class BudgetContextOrchestrator:
    """组合预算评估、snip 与 compact，统一处理上下文治理编排。

    典型工作流如下：
    1. `run_pre_model_cycle()` 在真正请求模型前执行预算预检、soft-over snip 与 auto compact。
    2. `complete_with_reactive_compact()` 在模型调用失败且命中 context-length 错误时执行 reactive compact 重试。
    3. 上层消费 `PreModelContextOutcome` 与 `ReactiveCompactOutcome`，统一推进主循环状态。
    """

    budget_evaluator: ContextTokenBudgetEvaluator  # ContextTokenBudgetEvaluator：负责生成 token 预算快照。
    context_snipper: ContextSnipper  # ContextSnipper：负责在 soft-over 时做轻量级上下文剪裁。
    context_compactor: ContextCompactor  # ContextCompactor：负责主动与被动的摘要压缩。

    def run_pre_model_cycle(
        self,
        *,
        session_state: _SessionStateLike,
        budget_config: BudgetConfig,
        context_policy: ContextPolicy,
        guard: BudgetGuard,
        openai_tools: list[JSONDict],
        turn_index: int,
        turns_offset: int,
        turns_this_run: int,
        usage_delta: TokenUsage,
        model_call_count: int,
    ) -> PreModelContextOutcome:
        """执行模型调用前的上下文治理编排并返回统一结果。

        Args:
            session_state (_SessionStateLike): 当前会话状态对象，提供消息列表与消息副本视图。
            budget_config (BudgetConfig): 当前预算配置对象。
            context_policy (ContextPolicy): 当前上下文治理策略对象。
            guard (BudgetGuard): 预算闸门对象，用于统一判断是否需要停止。
            openai_tools (list[JSONDict]): 当前可见工具定义列表。
            turn_index (int): 当前 turn 序号，用于写入事件。
            turns_offset (int): 本轮运行前已完成的历史 turn 数。
            turns_this_run (int): 当前 run/resume 调用内已完成的 turn 数。
            usage_delta (TokenUsage): 当前 run/resume 调用累计的 usage 增量。
            model_call_count (int): 当前 run/resume 调用累计的模型调用次数。
        Returns:
            PreModelContextOutcome: 包含最新预算快照、计数、停止原因和事件的治理结果。
        """
        events: list[JSONDict] = []
        next_usage_delta = usage_delta
        next_model_call_count = model_call_count

        snapshot = self.budget_evaluator.evaluate(
            messages=session_state.to_messages(),
            tools=openai_tools,
            max_input_tokens=budget_config.max_input_tokens,
        )

        if snapshot.is_soft_over:
            snip_result = self.context_snipper.snip(
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
                snapshot = self.budget_evaluator.evaluate(
                    messages=session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=budget_config.max_input_tokens,
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
                context_policy.auto_compact_threshold_tokens,
            )
            and pre_model_stop is None
        ):
            compact_result = self.context_compactor.compact(
                session_state.messages,
                preserve_messages=context_policy.compact_preserve_messages,
            )
            if compact_result.compacted:
                next_model_call_count += 1
                next_usage_delta = next_usage_delta + compact_result.usage
                events.append(self._make_compact_event(turn_index, 'auto', compact_result))
                snapshot = self.budget_evaluator.evaluate(
                    messages=session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=budget_config.max_input_tokens,
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
        budget_config: BudgetConfig,
        context_policy: ContextPolicy,
        openai_tools: list[JSONDict],
        turn_index: int,
        guard: BudgetGuard,
        turns_offset: int,
        turns_this_run: int,
        usage_delta: TokenUsage,
        model_call_count: int,
    ) -> ReactiveCompactOutcome:
        """执行模型调用，并在需要时做 reactive compact 重试。

        Args:
            client (OpenAIClient): 用于发起模型调用的客户端。
            session_state (_SessionStateLike): 当前会话状态对象，提供消息列表与消息副本视图。
            budget_config (BudgetConfig): 当前预算配置对象。
            context_policy (ContextPolicy): 当前上下文治理策略对象。
            openai_tools (list[JSONDict]): 当前可见工具定义列表。
            turn_index (int): 当前 turn 序号，用于写入事件。
            guard (BudgetGuard): 预算闸门对象，用于在重试后重新检查预算停止条件。
            turns_offset (int): 本轮运行前已完成的历史 turn 数。
            turns_this_run (int): 当前 run/resume 调用内已完成的 turn 数。
            usage_delta (TokenUsage): 当前 run/resume 调用累计的 usage 增量。
            model_call_count (int): 当前 run/resume 调用累计的模型调用次数。
        Returns:
            ReactiveCompactOutcome: 包含最终响应、累计用量、计数、停止原因和事件的调用结果。
        """
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
                    output_schema=context_policy.output_schema,
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
                    context_policy.compact_preserve_messages - (attempt - 1),
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
                    max_input_tokens=budget_config.max_input_tokens,
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
        """统一构造 compact_boundary 事件。

        Args:
            turn_index (int): 当前 turn 序号。
            trigger (str): 当前 compact 的触发方式，如 `auto` 或 `reactive`。
            result (CompactionResult): compact 执行结果。
            attempt (int | None): reactive compact 场景下的重试次数。
        Returns:
            JSONDict: 统一格式的 compact_boundary 事件载荷。
        """
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
