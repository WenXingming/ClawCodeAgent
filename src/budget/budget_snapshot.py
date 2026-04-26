"""ISSUE-009 Token Budget 快照对象。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenBudgetSnapshot:
    """描述一次 token 预算预检的结果快照。"""

    projected_input_tokens: int
    output_reserve_tokens: int
    hard_input_limit: int | None
    soft_input_limit: int | None
    is_hard_over: bool
    is_soft_over: bool