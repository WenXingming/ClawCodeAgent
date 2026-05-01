"""context 领域统一网关。

本模块是 context 目录对外的唯一公开入口，隔离全部内部实现细节：
1. project_budget()                  —— 预算投影：评估本次调用的 token 预算快照；
2. run_pre_model_cycle()             —— pre-model 治理：snip → auto-compact → guard 完整链路；
3. complete_with_reactive_compact()  —— 带恢复能力的模型调用：context error 后最多重试两轮。

外部代码只能通过 context/__init__.py 的 ContextGateway 访问本包能力；
其余内部类型（BudgetProjector、Snipper、Compactor 等）仅供单元测试通过本模块路径访问。
"""

from __future__ import annotations

from core_contracts.config import BudgetConfig
from core_contracts.context_contracts import (
    BudgetProjection,
    CompactionResult,
    ContextRunState,
    PreModelBudgetGuard,
    PreModelContextOutcome,
    ReactiveCompactOutcome,
    SnipResult,
)
from core_contracts.model import ModelClient
from core_contracts.primitives import JSONDict
from core_contracts.config import ContextPolicy

from .budget_projection import BudgetProjector, OUTPUT_RESERVE_TOKENS, SOFT_BUFFER_TOKENS
from .compactor import Compactor
from .token_estimator import TokenEstimator
from .snipper import Snipper


_MAX_REACTIVE_COMPACT_RETRIES: int = 2  # reactive compact 允许的最大重试轮次。


class ContextGateway:
    """对外暴露 context 治理能力并严格隔离内部实现细节的网关类。

    核心工作流：
    1. project_budget() 计算当前消息和工具定义的 token 预算快照；
    2. run_pre_model_cycle() 执行 snip → auto-compact → pre-model guard 编排；
    3. complete_with_reactive_compact() 发起模型调用并在 context error 时执行恢复性重试。
    """

    def __init__(
        self,
        *,
        client: ModelClient | None,
        budget_projector: BudgetProjector,
        snipper: Snipper,
        compactor: Compactor | None,
    ) -> None:
        """通过依赖注入初始化 context 网关。

        所有内部构件由外部（工厂函数）构造后注入；网关本身不负责 new 任何内部类。

        Args:
            client (ModelClient | None): 模型客户端；为 None 时仅支持预算投影，
                                         compact 与 reactive 路径调用时抛出 RuntimeError。
            budget_projector (BudgetProjector): token 预算投影器实例。
            snipper (Snipper): 轻量剪裁器实例，负责 tombstone 化降载。
            compactor (Compactor | None): 摘要压缩器实例；无客户端时传 None。
        Returns:
            None: 构造函数无返回值。
        Raises:
            无。
        """
        self._client: ModelClient | None = client
        # ModelClient | None：注入的模型客户端；compact 及 reactive 调用的统一出口。

        self._budget_projector: BudgetProjector = budget_projector
        # BudgetProjector：token 预算投影器，用于预检输入上下文成本。

        self._snipper: Snipper = snipper
        # Snipper：轻量剪裁器，用于 soft-over 阶段的 tombstone 化降载。

        self._compactor: Compactor | None = compactor
        # Compactor | None：摘要压缩器；无客户端时保持禁用，compact 方法不可用。

    def project_budget(
        self,
        messages: list[JSONDict],
        *,
        tools: list[JSONDict] | None = None,
        max_input_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        soft_buffer_tokens: int | None = None,
    ) -> BudgetProjection:
        """对当前消息和工具集合执行预算投影。

        Args:
            messages (list[JSONDict]): 当前会话消息列表。
            tools (list[JSONDict] | None): 当前可见工具 schema 列表；None 等同于空列表。
            max_input_tokens (int | None): 输入 token 硬上限；None 表示不设硬限制。
            output_reserve_tokens (int | None): 输出预留 token 覆盖值；None 使用默认值。
            soft_buffer_tokens (int | None): 软缓冲 token 覆盖值；None 使用默认值。
        Returns:
            BudgetProjection: 预算快照，含 projected/hard/soft 投影量与 over 标记。
        Raises:
            无。
        """
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
        """执行模型调用前的完整上下文治理编排并返回统一结果。

        治理顺序：投影预算 → soft-over 时 snip → pre-model guard → auto-compact（如触发阈值）→ 再次 guard。

        Args:
            run_state (ContextRunState): 当前 turn 的运行态协议对象（就地更新 token 统计）。
            budget_config (BudgetConfig): 预算限制配置（硬限、输出预留等）。
            context_policy (ContextPolicy): 上下文治理策略（compact 阈值、保留消息数等）。
            guard (PreModelBudgetGuard): pre-model 预算守卫，决定是否允许继续调用模型。
            openai_tools (list[JSONDict]): 当前可见工具 schema 列表，纳入 token 估算。
        Returns:
            PreModelContextOutcome: pre-model 阶段的 stop reason 与有序事件集合。
        Raises:
            RuntimeError: 需要执行 auto-compact 但网关未配置模型客户端时抛出。
        """
        events: list[JSONDict] = []
        turn_index = run_state.turn_index
        next_usage_delta = run_state.usage_delta
        next_model_call_count = run_state.model_call_count
        session_state = run_state.session_state

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
                events.append(self._make_snip_event(turn_index, snip_result))
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

        if pre_model_stop is None:
            compactor = self._require_compactor()
            if compactor.should_auto_compact(
                snapshot.projected_input_tokens,
                context_policy.auto_compact_threshold_tokens,
            ):
                compact_result = compactor.compact(
                    session_state.messages,
                    preserve_messages=context_policy.compact_preserve_messages,
                )
                if compact_result.compacted:
                    next_model_call_count += 1
                    next_usage_delta = next_usage_delta + compact_result.usage
                    events.append(
                        self._make_compact_event(turn_index, 'auto', compact_result)
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
                elif compact_result.error:
                    events.append(
                        self._make_compact_failed_event(
                            turn_index, 'auto', compact_result.error,
                            context_policy.compact_preserve_messages,
                        )
                    )

        events.append(self._make_token_budget_event(turn_index, snapshot))

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
        """发起模型调用并在 context length 错误时执行 reactive compact 重试。

        重试策略：每轮逐步收紧 preserve_messages（最少保留 1 条），最多重试两轮。
        若重试后预算守卫仍拒绝继续，则提前返回 stop_reason 而非再次调用模型。

        Args:
            run_state (ContextRunState): 当前 turn 的运行态协议对象（就地更新 token 统计）。
            budget_config (BudgetConfig): 预算限制配置。
            context_policy (ContextPolicy): 上下文治理策略（compact 保留消息数等）。
            openai_tools (list[JSONDict]): 当前可见工具 schema 列表。
            guard (PreModelBudgetGuard): pre-model 预算守卫，用于重试后的二次检查。
        Returns:
            ReactiveCompactOutcome: 模型响应（成功时）或 stop_reason（守卫拒绝时）及事件序列。
        Raises:
            RuntimeError: 当网关未配置模型客户端时抛出。
        """
        events: list[JSONDict] = []
        session_state = run_state.session_state
        turn_index = run_state.turn_index
        current_usage = run_state.usage_delta
        current_model_call_count = run_state.model_call_count
        attempt = 0
        current_error: Exception | None = None
        client = self._require_client()
        compactor = self._require_compactor()

        while True:
            try:
                response = client.complete(
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
                if (
                    not compactor.is_context_length_error(exc)
                    or attempt >= _MAX_REACTIVE_COMPACT_RETRIES
                ):
                    break

                attempt += 1
                preserve_messages = max(
                    1, context_policy.compact_preserve_messages - (attempt - 1)
                )
                compact_result = compactor.compact(
                    session_state.messages,
                    preserve_messages=preserve_messages,
                )

                if not compact_result.compacted:
                    events.append(
                        self._make_retry_event(
                            turn_index, attempt, preserve_messages, str(exc),
                            ok=False,
                            error=compact_result.error or 'Reactive compact made no progress',
                        )
                    )
                    break

                current_model_call_count += 1
                current_usage = current_usage + compact_result.usage
                events.append(
                    self._make_compact_event(turn_index, 'reactive', compact_result, attempt=attempt)
                )
                events.append(
                    self._make_retry_event(
                        turn_index, attempt, preserve_messages, str(exc),
                        ok=True,
                        tokens_removed=compact_result.tokens_removed,
                        messages_replaced=compact_result.messages_replaced,
                    )
                )

                snapshot = self.project_budget(
                    session_state.to_messages(),
                    tools=openai_tools,
                    max_input_tokens=budget_config.max_input_tokens,
                )
                run_state.token_budget_snapshot = snapshot
                stop_reason = guard.check_pre_model(
                    turns_offset=run_state.turns_offset,
                    turns_this_run=run_state.turns_this_run,
                    model_call_count=current_model_call_count,
                    snapshot=snapshot,
                    usage_delta=current_usage,
                )
                if stop_reason is not None:
                    run_state.usage_delta = current_usage
                    run_state.model_call_count = current_model_call_count
                    return ReactiveCompactOutcome(
                        response=None,
                        stop_reason=stop_reason,
                        events=tuple(events),
                    )

        events.append(self._make_backend_error_event(turn_index, str(current_error)))
        run_state.usage_delta = current_usage
        run_state.model_call_count = current_model_call_count
        return ReactiveCompactOutcome(
            response=None,
            stop_reason=None,
            events=tuple(events),
        )

    def _require_client(self) -> ModelClient:
        """返回注入的模型客户端；未配置时抛出显式错误。

        Args:
            无。
        Returns:
            ModelClient: 当前网关绑定的模型客户端。
        Raises:
            RuntimeError: 当网关初始化时未提供模型客户端时抛出。
        """
        if self._client is None:
            raise RuntimeError(
                'ContextGateway requires a model client for model call operations'
            )
        return self._client

    def _require_compactor(self) -> Compactor:
        """返回 compact 能力实例；未配置客户端时抛出显式错误。

        Args:
            无。
        Returns:
            Compactor: 当前网关绑定的摘要压缩器。
        Raises:
            RuntimeError: 当网关初始化时未提供模型客户端，导致 compact 不可用时抛出。
        """
        if self._compactor is None:
            raise RuntimeError(
                'ContextGateway requires a model client for compact operations'
            )
        return self._compactor

    @staticmethod
    def _make_compact_event(
        turn_index: int,
        trigger: str,
        result: CompactionResult,
        *,
        attempt: int | None = None,
    ) -> JSONDict:
        """统一构造 compact_boundary 事件字典。

        Args:
            turn_index (int): 当前 turn 的序号。
            trigger (str): compact 触发源，'auto' 或 'reactive'。
            result (CompactionResult): compact 执行结果。
            attempt (int | None): reactive 重试的序号；auto 场景传 None。
        Returns:
            JSONDict: 可直接写入事件流的 compact_boundary 事件字典。
        Raises:
            无。
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

    @staticmethod
    def _make_snip_event(
        turn_index: int,
        result: SnipResult,
    ) -> JSONDict:
        """统一构造 snip_boundary 事件字典。

        Args:
            turn_index (int): 当前 turn 的序号。
            result (SnipResult): snip 执行结果。
        Returns:
            JSONDict: 可直接写入事件流的 snip_boundary 事件字典。
        Raises:
            无。
        """
        return {
            'type': 'snip_boundary',
            'turn': turn_index,
            'snipped_count': result.snipped_count,
            'tokens_removed': result.tokens_removed,
        }

    @staticmethod
    def _make_compact_failed_event(
        turn_index: int,
        trigger: str,
        error: str,
        preserve_messages: int,
    ) -> JSONDict:
        """统一构造 compact_failed 事件字典。

        Args:
            turn_index (int): 当前 turn 的序号。
            trigger (str): compact 触发源。
            error (str): compact 失败原因。
            preserve_messages (int): compact 使用的尾部保留消息数。
        Returns:
            JSONDict: 可直接写入事件流的 compact_failed 事件字典。
        Raises:
            无。
        """
        return {
            'type': 'compact_failed',
            'turn': turn_index,
            'trigger': trigger,
            'error': error,
            'preserve_messages': preserve_messages,
        }

    @staticmethod
    def _make_token_budget_event(
        turn_index: int,
        snapshot: BudgetProjection,
    ) -> JSONDict:
        """统一构造 token_budget 事件字典。

        Args:
            turn_index (int): 当前 turn 的序号。
            snapshot (BudgetProjection): 当前预算快照。
        Returns:
            JSONDict: 可直接写入事件流的 token_budget 事件字典。
        Raises:
            无。
        """
        return {
            'type': 'token_budget',
            'turn': turn_index,
            'projected': snapshot.projected_input_tokens,
            'is_hard_over': snapshot.is_hard_over,
            'is_soft_over': snapshot.is_soft_over,
        }

    @staticmethod
    def _make_backend_error_event(
        turn_index: int,
        error: str,
    ) -> JSONDict:
        """统一构造 backend_error 事件字典。

        Args:
            turn_index (int): 当前 turn 的序号。
            error (str): 错误描述文本。
        Returns:
            JSONDict: 可直接写入事件流的 backend_error 事件字典。
        Raises:
            无。
        """
        return {
            'type': 'backend_error',
            'turn': turn_index,
            'error': error,
        }

    @staticmethod
    def _make_retry_event(
        turn_index: int,
        attempt: int,
        preserve_messages: int,
        context_error: str,
        ok: bool,
        *,
        error: str | None = None,
        tokens_removed: int | None = None,
        messages_replaced: int | None = None,
    ) -> JSONDict:
        """统一构造 reactive_compact_retry 事件字典。

        Args:
            turn_index (int): 当前 turn 的序号。
            attempt (int): reactive 重试的序号。
            preserve_messages (int): 本次 retry 使用的尾部保留消息数。
            context_error (str): 触发 retry 的原始上下文错误文本。
            ok (bool): retry 的 compact 是否成功。
            error (str | None): compact 失败时的错误原因（ok=False 时必填）。
            tokens_removed (int | None): compact 成功时移除的 token 数。
            messages_replaced (int | None): compact 成功时替换的消息数。
        Returns:
            JSONDict: 可直接写入事件流的 reactive_compact_retry 事件字典。
        Raises:
            无。
        """
        event: JSONDict = {
            'type': 'reactive_compact_retry',
            'turn': turn_index,
            'attempt': attempt,
            'preserve_messages': preserve_messages,
            'context_error': context_error,
            'ok': ok,
        }
        if error is not None:
            event['error'] = error
        if tokens_removed is not None:
            event['tokens_removed'] = tokens_removed
        if messages_replaced is not None:
            event['messages_replaced'] = messages_replaced
        return event


