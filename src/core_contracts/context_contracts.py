"""context 域跨模块共享契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core_contracts.protocol import JSONDict
from core_contracts.token_usage import TokenUsage


@dataclass(frozen=True)
class BudgetProjection:
    """描述一次 token 预算预检的结果快照。"""

    projected_input_tokens: int
    output_reserve_tokens: int
    hard_input_limit: int | None
    soft_input_limit: int | None
    is_hard_over: bool
    is_soft_over: bool


class SessionMessageView(Protocol):
    """context 预算与 compact 流程需要的最小会话视图。"""

    messages: list[JSONDict]

    def to_messages(self) -> list[JSONDict]:
        """返回标准模型消息列表。"""


class ContextRunState(Protocol):
    """context 门面所需的最小运行态协议。"""

    session_state: SessionMessageView
    turn_index: int
    usage_delta: TokenUsage
    model_call_count: int
    turns_offset: int
    turns_this_run: int
    token_budget_snapshot: BudgetProjection | None


class PreModelBudgetGuard(Protocol):
    """context 预检所需预算守卫最小协议。"""

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
