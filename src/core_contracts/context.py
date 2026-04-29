"""上下文治理跨模块契约。

定义上下文预算、snip、compact 及 reactive compact 流程中跨边界共享的
DTO、Protocol 和结果对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .primitives import JSONDict
from .messaging import OneTurnResponse
from .primitives import TokenUsage


@dataclass(frozen=True)
class BudgetProjection:
    """描述一次 token 预算预检的结果快照。"""

    projected_input_tokens: int  # int：本次调用预估消耗的输入 token 总量。
    output_reserve_tokens: int  # int：从输入上限中预留给模型输出的 token 数。
    hard_input_limit: int | None  # int | None：输入 token 硬上限；None 表示不设限。
    soft_input_limit: int | None  # int | None：输入 token 软上限；None 表示不设限。
    is_hard_over: bool  # bool：投影是否已超出硬上限可用空间。
    is_soft_over: bool  # bool：投影是否已超出软上限可用空间。


@dataclass(frozen=True)
class SnipResult:
    """描述一次 snip 操作的统计结果。"""

    snipped_count: int  # int：本次被 tombstone 化的消息条数。
    tokens_removed: int  # int：本次操作估算节省的 token 数量。


@dataclass(frozen=True)
class CompactionResult:
    """描述一次 compact 操作的完整结果。"""

    compacted: bool  # bool：本次 compact 是否实际执行并写回。
    summary_text: str = ''  # str：模型生成的摘要文本；未成功时为空字符串。
    messages_replaced: int = 0  # int：被摘要替换掉的原始消息条数。
    tokens_removed: int = 0  # int：compact 后估算节省的 token 数量。
    pre_tokens: int = 0  # int：compact 前消息列表的估算 token 总量。
    post_tokens: int = 0  # int：compact 后消息列表的估算 token 总量。
    preserve_messages_used: int = 0  # int：本次实际保留的尾部消息条数。
    usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：compact 调用消耗的模型 token 统计。
    error: str | None = None  # str | None：失败原因描述；成功时为 None。


class SessionMessageView(Protocol):
    """context 预算与 compact 流程所需的最小会话视图协议。"""

    messages: list[JSONDict]  # list[JSONDict]：可变的原始消息列表，供 snip/compact 就地修改。

    def to_messages(self) -> list[JSONDict]:
        """返回标准模型消息列表。"""


class ContextRunState(Protocol):
    """context 网关编排接口所需的最小运行态协议。"""

    session_state: SessionMessageView  # SessionMessageView：当前 turn 的会话消息视图。
    turn_index: int  # int：当前 turn 在整个 run 中的序号（0-based）。
    usage_delta: TokenUsage  # TokenUsage：本次 run 累计消耗的模型 token 量。
    model_call_count: int  # int：本次 run 已发起的模型调用次数。
    turns_offset: int  # int：本次 run 相对于会话起始的 turn 偏移量。
    turns_this_run: int  # int：本次 run 已完成的 turn 总数。
    token_budget_snapshot: BudgetProjection | None  # BudgetProjection | None：最近一次预算快照。


class PreModelBudgetGuard(Protocol):
    """context 预检流程所需的预算守卫最小协议。"""

    def check_pre_model(
        self,
        *,
        turns_offset: int,
        turns_this_run: int,
        model_call_count: int,
        snapshot: BudgetProjection,
        usage_delta: TokenUsage,
    ) -> str | None:
        """执行 pre-model 预算检查并返回 stop reason。"""


@dataclass(frozen=True)
class PreModelContextOutcome:
    """表示一次 pre-model 上下文治理阶段的结果快照。"""

    pre_model_stop: str | None  # str | None：预算守卫给出的停止原因；允许继续时为 None。
    events: tuple[JSONDict, ...]  # tuple[JSONDict, ...]：本轮 pre-model 阶段产生的有序事件序列。


@dataclass(frozen=True)
class ReactiveCompactOutcome:
    """表示一次模型调用及 reactive compact 重试链路的完整结果。"""

    response: OneTurnResponse | None  # OneTurnResponse | None：成功时的模型响应；失败或提前停止时为 None。
    stop_reason: str | None  # str | None：预算守卫给出的停止原因；未触发时为 None。
    events: tuple[JSONDict, ...]  # tuple[JSONDict, ...]：reactive compact 阶段的有序事件序列。
