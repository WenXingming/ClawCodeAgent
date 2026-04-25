"""ISSUE-009 Token Budget 预检与投影估算。

本模块聚焦两件事：
1. 基于 char/4 启发式估算消息与工具定义的输入 token 投影。
2. 生成 `TokenBudgetSnapshot`，供 runtime 在模型调用前执行软/硬阈值判断。

文件内定义按“公开主入口优先，再顺着调用链往下读”的顺序组织，便于沿
`check_token_budget()` 这条主线理解预算判断过程。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# 为模型输出保留的 token 空间，不计入可用输入预算。
OUTPUT_RESERVE_TOKENS: int = 4_096

# auto-compact 触发缓冲：projected 超过 (hard_limit - OUTPUT_RESERVE - SOFT_BUFFER)
# 时置 is_soft_over=True，由 ISSUE-010/011 决定是否执行 snip / compact。
SOFT_BUFFER_TOKENS: int = 13_000

_CHARS_PER_TOKEN: int = 4   # 估算用字符/token 比率（1 token ≈ 4 chars）。
_MSG_OVERHEAD: int = 4      # 每条消息的结构开销（role 标记、格式字节等）。
_CHAT_BASE: int = 3         # 整个消息列表的基础 token 开销。


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
    projected_input_tokens: int  # int：messages 与 tools 合并后的投影输入 token 数。
    output_reserve_tokens: int  # int：为模型输出预留、不可被输入占用的 token 数。
    hard_input_limit: int | None  # int | None：硬输入上限；None 表示不限制。
    soft_input_limit: int | None  # int | None：触发 snip/compact 的软阈值；None 表示不限制。
    is_hard_over: bool  # bool：是否已经超过硬输入上限。
    is_soft_over: bool  # bool：是否已经超过软阈值。

def check_token_budget(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_input_tokens: int | None = None,
    output_reserve: int = OUTPUT_RESERVE_TOKENS,
    soft_buffer: int = SOFT_BUFFER_TOKENS,
) -> TokenBudgetSnapshot:
    """生成 token 预算快照。

    Args:
        messages (list[dict[str, Any]]): 当前消息列表，通常来自 `AgentSessionState.to_messages()`。
        tools (list[dict[str, Any]] | None): 发送给模型的工具定义列表；None 等价于空列表。
        max_input_tokens (int | None): 输入 token 的硬上限；None 表示不限制。
        output_reserve (int): 为模型输出预留的 token 数。
        soft_buffer (int): 从硬上限中再扣除的缓冲区，用于提前触发 snip/compact。

    Returns:
        TokenBudgetSnapshot: 单次预算预检结果。
            当 `is_hard_over=True` 时，主循环应直接停止并返回 `token_limit`；
            当 `is_soft_over=True` 时，可由 snip/compact 模块尝试缓解上下文压力。
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


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """估算整个消息列表的总 token 数。

    Args:
        messages (list[dict[str, Any]]): 需要估算的消息列表。

    Returns:
        int: 估算得到的总 token 数，包含消息列表级别的基础结构开销。
    """
    return _CHAT_BASE + sum(estimate_message_tokens(message) for message in messages)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """估算单条消息的 token 数。

    Args:
        message (dict[str, Any]): 单条消息对象，`content` 支持字符串、多模态块列表或任意可序列化值。

    Returns:
        int: 该消息的估算 token 数，包含 role 与消息结构开销。
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


def estimate_tools_tokens(tools: list[dict[str, Any]]) -> int:
    """估算工具 schema 占用的 token 数。

    Args:
        tools (list[dict[str, Any]]): 发送给模型的工具定义列表。

    Returns:
        int: 序列化全部工具定义后估算得到的 token 数；空列表返回 0。
    """
    if not tools:
        return 0
    serialized = json.dumps(tools, ensure_ascii=False)
    return _estimate_str_tokens(serialized)


def _estimate_str_tokens(text: str) -> int:
    """基于 char/4 启发式估算字符串的 token 数。

    Args:
        text (str): 待估算的原始字符串。

    Returns:
        int: 估算得到的 token 数，最小返回 1。
    """
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)
