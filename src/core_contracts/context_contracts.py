"""context 域跨模块共享契约。

本模块集中定义 context 包对外暴露或跨域共享的全部 DTO、Protocol 和结果契约。
外部代码应仅通过本模块使用这些类型，禁止直接引用 context 内部实现模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core_contracts.protocol import JSONDict, OneTurnResponse
from core_contracts.token_usage import TokenUsage


@dataclass(frozen=True)
class BudgetProjection:
    """描述一次 token 预算预检的结果快照。

    由 BudgetProjector.project() 生成，贯穿 snip / compact / guard 全链路。
    """

    projected_input_tokens: int   # int：本次调用预估消耗的输入 token 总量。
    output_reserve_tokens: int    # int：从输入上限中预留给模型输出的 token 数。
    hard_input_limit: int | None  # int | None：输入 token 硬上限；None 表示不设限。
    soft_input_limit: int | None  # int | None：输入 token 软上限；None 表示不设限。
    is_hard_over: bool            # bool：投影是否已超出硬上限可用空间。
    is_soft_over: bool            # bool：投影是否已超出软上限可用空间。


@dataclass(frozen=True)
class SnipResult:
    """描述一次 snip 操作的统计结果。

    由 Snipper.snip() 返回，用于事件记录与日志追踪。
    """

    snipped_count: int    # int：本次被 tombstone 化的消息条数。
    tokens_removed: int   # int：本次操作估算节省的 token 数量。


@dataclass(frozen=True)
class CompactionResult:
    """描述一次 compact 操作的完整结果。

    由 Compactor.compact() 返回，包含成功标志、摘要内容、token 变化与模型用量。
    """

    compacted: bool                                   # bool：本次 compact 是否实际执行并写回。
    summary_text: str = ''                            # str：模型生成的摘要文本；未成功时为空字符串。
    messages_replaced: int = 0                        # int：被摘要替换掉的原始消息条数。
    tokens_removed: int = 0                           # int：compact 后估算节省的 token 数量。
    pre_tokens: int = 0                               # int：compact 前消息列表的估算 token 总量。
    post_tokens: int = 0                              # int：compact 后消息列表的估算 token 总量。
    preserve_messages_used: int = 0                   # int：本次实际保留的尾部消息条数。
    usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：compact 调用消耗的模型 token 统计。
    error: str | None = None                          # str | None：失败原因描述；成功时为 None。


class SessionMessageView(Protocol):
    """context 预算与 compact 流程所需的最小会话视图协议。

    仅暴露消息列表访问与序列化能力，对 context 包屏蔽完整会话实现。
    """

    messages: list[JSONDict]  # list[JSONDict]：可变的原始消息列表，供 snip/compact 就地修改。

    def to_messages(self) -> list[JSONDict]:
        """返回标准模型消息列表。
        Args:
            无。
        Returns:
            list[JSONDict]: 序列化后的消息列表副本，用于预算投影与模型调用。
        Raises:
            无。
        """


class ContextRunState(Protocol):
    """context 网关编排接口所需的最小运行态协议。

    通过协议而非具体类型依赖保证 context 包对 orchestration 实现的隔离。
    """

    session_state: SessionMessageView    # SessionMessageView：当前 turn 的会话消息视图。
    turn_index: int                      # int：当前 turn 在整个 run 中的序号（0-based）。
    usage_delta: TokenUsage              # TokenUsage：本次 run 累计消耗的模型 token 量。
    model_call_count: int                # int：本次 run 已发起的模型调用次数。
    turns_offset: int                    # int：本次 run 相对于会话起始的 turn 偏移量。
    turns_this_run: int                  # int：本次 run 已完成的 turn 总数。
    token_budget_snapshot: BudgetProjection | None  # BudgetProjection | None：最近一次预算快照；初始为 None。


class PreModelBudgetGuard(Protocol):
    """context 预检流程所需的预算守卫最小协议。

    实现方需提供 check_pre_model()，由网关在 snip/compact 后调用以判断是否允许继续。
    """

    def check_pre_model(
        self,
        *,
        turns_offset: int,
        turns_this_run: int,
        model_call_count: int,
        snapshot: BudgetProjection,
        usage_delta: TokenUsage,
    ) -> str | None:
        """执行 pre-model 预算检查并返回 stop reason。
        Args:
            turns_offset (int): 本次 run 的 turn 偏移量。
            turns_this_run (int): 本次 run 已完成 turn 数。
            model_call_count (int): 已发起的模型调用次数（含 compact）。
            snapshot (BudgetProjection): 当前上下文的预算快照。
            usage_delta (TokenUsage): 本次 run 累计 token 用量。
        Returns:
            str | None: 需要停止时返回 stop reason 字符串；允许继续时返回 None。
        Raises:
            无。
        """


@dataclass(frozen=True)
class PreModelContextOutcome:
    """表示一次 pre-model 上下文治理阶段的结果快照。

    由 ContextGateway.run_pre_model_cycle() 返回，调用方据此决定是否继续发起模型调用。
    """

    pre_model_stop: str | None       # str | None：预算守卫给出的停止原因；允许继续时为 None。
    events: tuple[JSONDict, ...]     # tuple[JSONDict, ...]：本轮 pre-model 阶段产生的有序事件序列。


@dataclass(frozen=True)
class ReactiveCompactOutcome:
    """表示一次模型调用及 reactive compact 重试链路的完整结果。

    由 ContextGateway.complete_with_reactive_compact() 返回，封装模型响应与所有重试事件。
    """

    response: OneTurnResponse | None  # OneTurnResponse | None：成功时的模型响应；失败或提前停止时为 None。
    stop_reason: str | None           # str | None：预算守卫给出的停止原因；未触发时为 None。
    events: tuple[JSONDict, ...]      # tuple[JSONDict, ...]：reactive compact 阶段的有序事件序列。
