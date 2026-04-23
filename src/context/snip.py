"""ISSUE-010 Snip 上下文剪裁：就地替换旧消息内容为 tombstone 摘要。

公共 API
--------
    SnipResult   — 本次剪裁统计（不可变数据类）。
    snip_session — 对 messages 列表就地剪裁，返回 SnipResult。

设计说明
--------
snip 的目标是在 is_soft_over=True 时降低 prompt 压力，以便本轮模型调用
仍能正常完成；它属于轻量处理，不压缩语义，只丢弃可恢复的"旧冗余内容"。

剪裁范围（从旧到新，依次处理）：
    跳过前缀 system 消息  — 由 _count_prefix() 计算
    跳过尾部最近 N 条     — 由 preserve_messages 参数控制（对应 compact_preserve_messages）
    中间段所有候选消息    — 由 _is_snippable() 判断

候选规则（_is_snippable）：
    role=tool                                 → 可剪（工具结果往往最长）
    role=assistant 且 tool_calls 非空          → 可剪（仅保留 tool_calls，清空 content）
    role=assistant 且 content 超过 300 字符   → 可剪（长输出，非关键的中间步骤）
    已经是 tombstone                          → 不可剪（避免重复处理）
    其余                                      → 不可剪

Tombstone 格式（_make_tombstone）：
    content 替换为 <system-reminder> 块；
    role / tool_call_id / tool_calls 等协议字段保留，确保消息链完整。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .token_budget import estimate_message_tokens

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# assistant 消息内容超过此长度才列为剪裁候选。
_LONG_ASSISTANT_THRESHOLD: int = 300

# tombstone 摘要的最大预览字符数。
_PREVIEW_MAX_CHARS: int = 120


# ---------------------------------------------------------------------------
# 公共数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SnipResult:
    """snip_session 的剪裁统计结果。

    Attributes:
        snipped_count:   本次被替换为 tombstone 的消息数量。
        tokens_removed:  估算节省的 token 数（原始消息 - tombstone 之差）。
    """

    snipped_count: int
    tokens_removed: int


# ---------------------------------------------------------------------------
# 公共函数
# ---------------------------------------------------------------------------

def snip_session(
    messages: list[dict[str, Any]],
    *,
    preserve_messages: int = 4,
    tools: list[dict[str, Any]] | None = None,
    max_input_tokens: int | None = None,
) -> SnipResult:
    """就地剪裁 messages，将候选旧消息替换为 tombstone，返回剪裁统计。

    Args:
        messages:          AgentSessionState.messages 的直接引用（就地修改）。
        preserve_messages: 尾部保留不剪裁的消息数量（对应 compact_preserve_messages）。
        tools:             当前 openai tools 定义列表（保留参数，暂未使用）。
        max_input_tokens:  最大输入 token 上限（保留参数，暂未使用）。

    Returns:
        SnipResult — 本次剪裁统计；snipped_count=0 表示无变化。
    """
    prefix = _count_prefix(messages)
    total = len(messages)
    # 保护尾部：最多保留 min(preserve_messages, available) 条
    tail = min(max(preserve_messages, 0), max(total - prefix, 0))
    # 可剪裁的索引范围：[prefix, total - tail)
    upper = total - tail

    snipped_count = 0
    tokens_removed = 0

    for i in range(prefix, upper):
        msg = messages[i]
        if not _is_snippable(msg):
            continue
        original_tokens = estimate_message_tokens(msg)
        tombstone = _make_tombstone(msg)
        tombstone_tokens = estimate_message_tokens(tombstone)
        messages[i] = tombstone
        snipped_count += 1
        tokens_removed += max(0, original_tokens - tombstone_tokens)

    return SnipResult(snipped_count=snipped_count, tokens_removed=tokens_removed)


# ---------------------------------------------------------------------------
# 私有辅助函数
# ---------------------------------------------------------------------------

def _count_prefix(messages: list[dict[str, Any]]) -> int:
    """返回消息列表头部连续 system 消息的数量（不可剪裁的前缀）。"""
    count = 0
    for msg in messages:
        if msg.get('role') == 'system':
            count += 1
        else:
            break
    return count


def _is_snippable(message: dict[str, Any]) -> bool:
    """判断消息是否为剪裁候选。

    tombstone 内容的特征是以 '<system-reminder>\\nOlder ' 开头，
    用来检测已被剪裁过的消息，避免重复处理。
    """
    # 已是 tombstone，跳过
    content = message.get('content', '')
    if isinstance(content, str) and content.startswith('<system-reminder>\nOlder '):
        return False

    role = message.get('role', '')

    if role == 'tool':
        return True

    if role == 'assistant':
        tool_calls = message.get('tool_calls')
        if tool_calls:
            return True
        # 长文本输出（中间步骤）
        text = content if isinstance(content, str) else json.dumps(content)
        if len(text) > _LONG_ASSISTANT_THRESHOLD:
            return True

    return False


def _make_tombstone(message: dict[str, Any]) -> dict[str, Any]:
    """将消息替换为 tombstone，保留协议字段，生成摘要 content。

    保留字段：role, tool_call_id（tool 消息）, tool_calls（assistant 消息）
    替换字段：content → <system-reminder> 摘要块
    """
    role = message.get('role', '')
    content = message.get('content', '')

    # 生成预览
    if isinstance(content, str):
        text = ' '.join(content.split())
    else:
        text = ' '.join(json.dumps(content).split())
    if len(text) > _PREVIEW_MAX_CHARS:
        text = text[:_PREVIEW_MAX_CHARS - 3] + '...'

    # 角色标签
    tool_calls = message.get('tool_calls')
    if role == 'tool':
        tool_name = message.get('name') or 'tool'
        label = f'tool result ({tool_name})'
    elif role == 'assistant' and tool_calls:
        label = 'assistant message with tool calls'
    else:
        label = role

    tombstone_content = (
        f'<system-reminder>\n'
        f'Older {label} was snipped to save context.\n'
        f'Preview: {text or "(empty)"}\n'
        f'</system-reminder>'
    )

    # 构造新消息，仅保留协议必要字段
    result: dict[str, Any] = {'role': role, 'content': tombstone_content}
    if role == 'tool':
        if 'tool_call_id' in message:
            result['tool_call_id'] = message['tool_call_id']
        if 'name' in message:
            result['name'] = message['name']
    elif role == 'assistant' and tool_calls:
        # 保留 tool_calls 使后续 tool 消息的 tool_call_id 不成为孤立引用
        result['tool_calls'] = tool_calls

    return result
