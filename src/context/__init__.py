"""ISSUE-009/010 上下文治理子包。

当前导出：
    TokenBudgetSnapshot  — 单次 token 预算预检结果（不可变数据类）。
    check_token_budget   — 根据 messages + tools + 预算限制生成快照。
    BudgetGuard          — 五维预算闸门管理。
    SnipResult           — snip_session 的剪裁统计结果。
    snip_session         — 就地剪裁消息列表，降低 prompt token 压力。

后续 ISSUE-011 (compact) 的公共接口也将在此导出。
"""

from .budget_guard import BudgetGuard
from .snip import SnipResult, snip_session
from .token_budget import TokenBudgetSnapshot, check_token_budget

__all__ = [
    'BudgetGuard',
    'SnipResult',
    'TokenBudgetSnapshot',
    'check_token_budget',
    'snip_session',
]
