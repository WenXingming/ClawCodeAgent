"""ISSUE-009/010 上下文治理子包。

当前导出：
    TokenBudgetSnapshot  — 单次 token 预算预检结果（不可变数据类）。
    check_token_budget   — 根据 messages + tools + 预算限制生成快照。
    BudgetGuard          — 五维预算闸门管理。
    SnipResult           — snip_session 的剪裁统计结果。
    snip_session         — 就地剪裁消息列表，降低 prompt token 压力。
    CompactResult        — compact_conversation 的摘要压缩结果。
    compact_conversation — 生成摘要并原地替换旧消息。
    is_context_length_error — 判断后端异常是否为 prompt/context length 类错误。
    should_auto_compact  — 判断是否达到 auto compact 阈值。

后续 ISSUE-011 (compact) 的公共接口也将在此导出。
"""

from .budget_guard import BudgetGuard
from .compact import (
    CompactResult,
    compact_conversation,
    is_context_length_error,
    should_auto_compact,
)
from .snip import SnipResult, snip_session
from .token_budget import TokenBudgetSnapshot, check_token_budget

__all__ = [
    'BudgetGuard',
    'CompactResult',
    'SnipResult',
    'TokenBudgetSnapshot',
    'check_token_budget',
    'compact_conversation',
    'is_context_length_error',
    'snip_session',
    'should_auto_compact',
]
