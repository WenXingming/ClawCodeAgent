"""ISSUE-009 上下文治理子包。

当前导出：
    TokenBudgetSnapshot  — 单次 token 预算预检结果（不可变数据类）。
    check_token_budget   — 根据 messages + tools + 预算限制生成快照。

后续 ISSUE-010 (snip) / ISSUE-011 (compact) 的公共接口也将在此导出。
"""

from .token_budget import TokenBudgetSnapshot, check_token_budget

__all__ = [
    'TokenBudgetSnapshot',
    'check_token_budget',
]
