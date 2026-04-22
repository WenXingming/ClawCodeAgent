"""ISSUE-009 Token Budget 预检：基于 char/4 启发式的 token 投影与多维度预算检查。

公共 API
--------
    TokenBudgetSnapshot  — 单次预检结果（不可变数据类）。
    check_token_budget   — 根据 messages + tools + 限制生成快照。

设计说明
--------
token 估算使用 **char/4 启发式**（1 token ≈ 4 chars）。
中文等宽字节语言下实际 token 数可能偏低；若需精确计数可替换
estimate_message_tokens 的内部实现，公共接口不变。

软超限（is_soft_over）供 ISSUE-010/011 的 snip / compact 模块使用，
硬超限（is_hard_over）触发主循环立即停止并返回 stop_reason='token_limit'。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 为模型输出保留的 token 空间，不计入可用输入预算。
OUTPUT_RESERVE_TOKENS: int = 4_096

# auto-compact 触发缓冲：projected 超过 (hard_limit - OUTPUT_RESERVE - SOFT_BUFFER)
# 时置 is_soft_over=True，由 ISSUE-010/011 决定是否执行 snip / compact。
SOFT_BUFFER_TOKENS: int = 13_000

_CHARS_PER_TOKEN: int = 4   # 估算用字符/token 比率（1 token ≈ 4 chars）。
_MSG_OVERHEAD: int = 4      # 每条消息的结构开销（role 标记、格式字节等）。
_CHAT_BASE: int = 3         # 整个消息列表的基础 token 开销。


# ---------------------------------------------------------------------------
# 内部估算辅助函数
# ---------------------------------------------------------------------------

def _estimate_str_tokens(text: str) -> int:
    """基于 char/4 估算字符串的 token 数，至少返回 1。"""
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# 公共估算函数
# ---------------------------------------------------------------------------

def estimate_message_tokens(message: dict[str, Any]) -> int:
    """估算单条消息的 token 数。

    content 支持：
    - str（普通文本）
    - list（多模态内容块，每块取 text 字段，否则 json 序列化兜底）
    - 其他类型：json 序列化后估算
    """
    content = message.get('content', '')
    if isinstance(content, str):
        content_tokens = _estimate_str_tokens(content) if content else 0
    elif isinstance(content, list):
        content_tokens = 0
        for block in content:
            if isinstance(block, dict):
                text = block.get('text', '')
                if isinstance(text, str) and text:
                    content_tokens += _estimate_str_tokens(text)
                else:
                    content_tokens += _estimate_str_tokens(json.dumps(block, ensure_ascii=False))
            else:
                content_tokens += _estimate_str_tokens(str(block))
    else:
        content_tokens = _estimate_str_tokens(json.dumps(content, ensure_ascii=False))

    role_tokens = _estimate_str_tokens(str(message.get('role', '')))
    return role_tokens + content_tokens + _MSG_OVERHEAD


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """估算整个消息列表的总 token 数（含基础结构开销）。"""
    return _CHAT_BASE + sum(estimate_message_tokens(m) for m in messages)


def estimate_tools_tokens(tools: list[dict[str, Any]]) -> int:
    """估算工具 schema 占用的 token 数（序列化后 char/4）。"""
    if not tools:
        return 0
    serialized = json.dumps(tools, ensure_ascii=False)
    return _estimate_str_tokens(serialized)


# ---------------------------------------------------------------------------
# 预检结果数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenBudgetSnapshot:
    """单次 token 预算预检结果（不可变）。

    Attributes:
        projected_input_tokens: 本轮投影输入 token 数（messages + tools 之和）。
        output_reserve_tokens:  为模型输出保留的 token 数（不计入可用输入）。
        hard_input_limit:       硬上限（来自 BudgetConfig.max_input_tokens）；
                                None 表示无限制。
        soft_input_limit:       软上限 = hard - output_reserve - soft_buffer；
                                None 表示无限制；最小值为 0。
        is_hard_over:           True 时主循环应立即 stop='token_limit'。
        is_soft_over:           True 时触发 snip / compact（ISSUE-010/011）。
    """
    projected_input_tokens: int
    output_reserve_tokens: int
    hard_input_limit: int | None
    soft_input_limit: int | None
    is_hard_over: bool
    is_soft_over: bool


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def check_token_budget(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_input_tokens: int | None = None,
    output_reserve: int = OUTPUT_RESERVE_TOKENS,
    soft_buffer: int = SOFT_BUFFER_TOKENS,
) -> TokenBudgetSnapshot:
    """生成 token 预算快照。

    Args:
        messages:         当前消息列表（AgentSessionState.to_messages() 的输出）。
        tools:            发送给模型的工具定义列表（可选）。
        max_input_tokens: 硬上限（来自 BudgetConfig.max_input_tokens）；
                          None 表示无限制。
        output_reserve:   为模型输出保留的 token 数（默认 OUTPUT_RESERVE_TOKENS）。
        soft_buffer:      触发 snip/compact 的余量缓冲（默认 SOFT_BUFFER_TOKENS）。

    Returns:
        TokenBudgetSnapshot。
        当 is_hard_over=True 时，主循环应 stop='token_limit'。
        当 is_soft_over=True 时，ISSUE-010/011 的 snip/compact 模块可介入。
    """
    projected = estimate_messages_tokens(messages) + estimate_tools_tokens(tools or [])

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
    usable = hard_limit - output_reserve          # 可用于输入的 token 上限
    soft_limit = max(0, usable - soft_buffer)     # 触发 snip/compact 的阈值

    return TokenBudgetSnapshot(
        projected_input_tokens=projected,
        output_reserve_tokens=output_reserve,
        hard_input_limit=hard_limit,
        soft_input_limit=soft_limit,
        is_hard_over=projected > usable,
        is_soft_over=projected > soft_limit,
    )
