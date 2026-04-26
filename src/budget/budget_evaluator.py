"""ISSUE-009 Token Budget 预检与投影估算。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from budget.budget_snapshot import TokenBudgetSnapshot
from budget.token_estimator import ContextTokenEstimator


OUTPUT_RESERVE_TOKENS: int = 4_096
SOFT_BUFFER_TOKENS: int = 13_000


@dataclass(frozen=True)
class ContextBudgetEvaluator:
    """基于 token 估算结果生成预算快照。"""

    token_estimator: ContextTokenEstimator = field(default_factory=ContextTokenEstimator)
    output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS
    soft_buffer_tokens: int = SOFT_BUFFER_TOKENS

    def evaluate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_input_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        soft_buffer_tokens: int | None = None,
    ) -> TokenBudgetSnapshot:
        output_reserve = self.output_reserve_tokens if output_reserve_tokens is None else output_reserve_tokens
        soft_buffer = self.soft_buffer_tokens if soft_buffer_tokens is None else soft_buffer_tokens
        projected = self.token_estimator.estimate_messages(messages) + self.token_estimator.estimate_tools(tools or [])

        if max_input_tokens is None:
            return TokenBudgetSnapshot(
                projected_input_tokens=projected,
                output_reserve_tokens=output_reserve,
                hard_input_limit=None,
                soft_input_limit=None,
                is_hard_over=False,
                is_soft_over=False,
            )

        hard_limit = max_input_tokens
        usable = hard_limit - output_reserve
        soft_limit = max(0, usable - soft_buffer)

        return TokenBudgetSnapshot(
            projected_input_tokens=projected,
            output_reserve_tokens=output_reserve,
            hard_input_limit=hard_limit,
            soft_input_limit=soft_limit,
            is_hard_over=projected > usable,
            is_soft_over=projected > soft_limit,
        )