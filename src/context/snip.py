"""ISSUE-010 Snip 上下文剪裁：把旧消息原地替换为 tombstone 摘要。

本模块负责在 `is_soft_over=True` 时做轻量级上下文瘦身。它不会压缩语义，只会把可恢复的旧消息内容替换成短摘要，以降低 prompt 压力并尽量保持消息链结构不变。

文件内定义按“公开入口优先，再顺着首次调用链往下读”的顺序组织，便于沿 `snip_session()` 这条主线理解剪裁范围与 tombstone 生成逻辑。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .token_budget import estimate_message_tokens

# assistant 消息内容超过此长度才列为剪裁候选。
_LONG_ASSISTANT_THRESHOLD: int = 300

# tombstone 摘要的最大预览字符数。
_PREVIEW_MAX_CHARS: int = 120

@dataclass(frozen=True)
class SnipResult:
    """snip_session 的剪裁统计结果。

    Attributes:
        snipped_count:   本次被替换为 tombstone 的消息数量。
        tokens_removed:  估算节省的 token 数（原始消息 - tombstone 之差）。
    """

    snipped_count: int  # int：本次被 tombstone 替换掉的消息数量。
    tokens_removed: int  # int：本次估算节省的 token 数。

def snip_session(
    messages: list[dict[str, Any]],
    *,
    preserve_messages: int = 4,
    tools: list[dict[str, Any]] | None = None,
    max_input_tokens: int | None = None,
) -> SnipResult:
    """就地剪裁 messages，将候选旧消息替换为 tombstone，返回剪裁统计。

    Args:
        messages (list[dict[str, Any]]): `AgentSessionState.messages` 的直接引用，会被原地修改。
        preserve_messages (int): 尾部保留不剪裁的消息数量。
        tools (list[dict[str, Any]] | None): 当前 openai tools 定义列表；预留给未来策略扩展。
        max_input_tokens (int | None): 最大输入 token 上限；预留给未来策略扩展。

    Returns:
        SnipResult: 本次剪裁统计；`snipped_count=0` 表示无变化。
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

def _count_prefix(messages: list[dict[str, Any]]) -> int:
    """返回头部连续 system 消息的数量。

    Args:
        messages (list[dict[str, Any]]): 当前会话消息列表。

    Returns:
        int: 不参与剪裁的前缀 system 消息数量。
    """
    count = 0
    for msg in messages:
        if msg.get('role') == 'system':
            count += 1
        else:
            break
    return count


def _is_snippable(message: dict[str, Any]) -> bool:
    """判断消息是否为剪裁候选。

    Args:
        message (dict[str, Any]): 待判断的单条消息。

    tombstone 内容的特征是以 '<system-reminder>\\nOlder ' 开头，
    用来检测已被剪裁过的消息，避免重复处理。

    Returns:
        bool: 当前消息是否允许被替换为 tombstone。
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
    """为单条消息生成 tombstone 替代内容。

    Args:
        message (dict[str, Any]): 原始消息对象。

    保留字段：role, tool_call_id（tool 消息）, tool_calls（assistant 消息）
    替换字段：content → <system-reminder> 摘要块

    Returns:
        dict[str, Any]: 保留协议字段后的 tombstone 消息对象。
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
