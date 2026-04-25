"""ISSUE-011 Compact 与 Reactive Compact：摘要压缩旧消息以释放上下文预算。

本模块负责把较早的消息压缩成两条 system reminder：一条记录“历史已被压缩”，另一条保存可继续执行任务所需的摘要。它既服务于主动 compact，也被 reactive compact 重试路径复用。

文件内定义按“公开主线优先，再顺着首次调用链往下读”的顺序组织，便于沿 `compact_conversation()` 这条主线理解请求构造、摘要清洗和消息替换流程。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from core_contracts.protocol import JSONDict
from core_contracts.usage import TokenUsage
from openai_client.openai_client import OpenAIClient, OpenAIClientError, OpenAIResponseError
from .token_budget import estimate_messages_tokens

# 标记“更早历史已被压缩”的 reminder 正文前缀。
_COMPACT_BOUNDARY_PREFIX = '<system-reminder>\nEarlier conversation history was compacted to save context.'

# 标记“压缩摘要正文”的 reminder 前缀。
_COMPACT_SUMMARY_PREFIX = '<system-reminder>\nCompact summary of earlier conversation:'

# 发送给 compact 模型的固定 system prompt。
_COMPACT_PROMPT = (
    'You are compressing earlier conversation history for a coding agent. '
    'Return plain text only. Summarize the essential state needed to continue the task. '
    'Include: user goal, important files or tools already used, key findings or edits, '
    'and the next concrete step. Do not ask follow-up questions. Do not call tools.'
)


@dataclass(frozen=True)
class CompactResult:
    """compact_conversation 的结果。"""

    compacted: bool  # bool：本次 compact 是否真的改写了消息列表。
    summary_text: str = ''  # str：模型生成并清洗后的摘要文本。
    messages_replaced: int = 0  # int：被 summary 替换掉的原始消息数量。
    tokens_removed: int = 0  # int：估算被释放出的 token 数。
    pre_tokens: int = 0  # int：compact 前消息列表的估算 token 数。
    post_tokens: int = 0  # int：compact 后消息列表的估算 token 数。
    preserve_messages_used: int = 0  # int：本次实际保留在尾部的消息数量。
    usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：本次 compact 额外消耗的模型 usage。
    error: str | None = None  # str | None：compact 未生效时的错误说明。


def compact_conversation(
    client: OpenAIClient,
    messages: list[JSONDict],
    *,
    preserve_messages: int = 4,
) -> CompactResult:
    """调用模型生成摘要，并把旧消息原地替换为 compact summary。

    Args:
        client (OpenAIClient): 用于发起 compact 模型调用的客户端。
        messages (list[JSONDict]): 当前会话消息列表，会被原地改写。
        preserve_messages (int): 尾部保留不参与 compact 的消息数量。

    Returns:
        CompactResult: 本次 compact 的结果统计；失败时 `compacted=False` 并带有错误信息。
    """
    request_messages = build_compact_request_messages(messages, preserve_messages=preserve_messages)
    if request_messages is None:
        return CompactResult(compacted=False, error='Not enough messages to compact')

    try:
        response = client.complete(messages=request_messages, tools=[])
    except OpenAIClientError as exc:
        return CompactResult(compacted=False, error=str(exc))

    if response.tool_calls:
        return CompactResult(compacted=False, error='Compact response unexpectedly requested tools')

    summary = format_compact_summary(response.content)
    if not summary:
        return CompactResult(compacted=False, usage=response.usage, error='Compact model returned empty summary')

    result = apply_compact_summary(
        messages,
        summary,
        preserve_messages=preserve_messages,
    )
    return CompactResult(
        compacted=result.compacted,
        summary_text=result.summary_text,
        messages_replaced=result.messages_replaced,
        tokens_removed=result.tokens_removed,
        pre_tokens=result.pre_tokens,
        post_tokens=result.post_tokens,
        preserve_messages_used=result.preserve_messages_used,
        usage=response.usage,
        error=result.error,
    )


def build_compact_request_messages(
    messages: list[JSONDict],
    *,
    preserve_messages: int = 4,
) -> list[JSONDict] | None:
    """构造发送给 compact 模型的请求消息。

    Args:
        messages (list[JSONDict]): 当前会话消息列表。
        preserve_messages (int): 尾部保留不参与 compact 的消息数量。

    Returns:
        list[JSONDict] | None: 可直接发送给模型的请求消息；若没有可压缩区间则返回 None。
    """
    prefix = _count_system_prefix(messages)
    total = len(messages)
    tail = min(max(preserve_messages, 0), max(total - prefix, 0))
    upper = total - tail

    if upper <= prefix:
        return None

    candidate_messages = messages[prefix:upper]
    rendered_history = _render_messages(candidate_messages)
    if not rendered_history:
        return None

    return [
        {'role': 'system', 'content': _COMPACT_PROMPT},
        {
            'role': 'user',
            'content': (
                'Summarize the following earlier conversation history for future turns.\n\n'
                f'{rendered_history}'
            ),
        },
    ]


def format_compact_summary(summary: str) -> str:
    """清理 compact 返回的摘要文本。

    Args:
        summary (str): 模型返回的原始摘要文本。

    Returns:
        str: 去除首尾空白并折叠多余空行后的摘要文本。
    """
    text = summary.strip()
    if not text:
        return ''
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def apply_compact_summary(
    messages: list[JSONDict],
    summary_text: str,
    *,
    preserve_messages: int = 4,
) -> CompactResult:
    """把消息中间段替换为 compact boundary 与 summary。

    Args:
        messages (list[JSONDict]): 当前会话消息列表，会被原地改写。
        summary_text (str): 需要写入 reminder 的摘要文本。
        preserve_messages (int): 尾部保留不参与 compact 的消息数量。

    Returns:
        CompactResult: 本次消息替换的结果统计；若未发生替换则返回错误说明。
    """
    formatted_summary = format_compact_summary(summary_text)
    if not formatted_summary:
        return CompactResult(compacted=False, error='Compact summary is empty')

    prefix = _count_system_prefix(messages)
    total = len(messages)
    tail = min(max(preserve_messages, 0), max(total - prefix, 0))
    upper = total - tail

    if upper <= prefix:
        return CompactResult(compacted=False, error='Not enough messages to compact')

    pre_tokens = estimate_messages_tokens(messages)
    replacement_count = upper - prefix
    boundary_message = {
        'role': 'system',
        'content': f'{_COMPACT_BOUNDARY_PREFIX}\n</system-reminder>',
    }
    summary_message = {
        'role': 'system',
        'content': f'{_COMPACT_SUMMARY_PREFIX}\n{formatted_summary}\n</system-reminder>',
    }

    new_messages = messages[:prefix] + [boundary_message, summary_message] + messages[upper:]
    post_tokens = estimate_messages_tokens(new_messages)
    messages[:] = new_messages

    return CompactResult(
        compacted=True,
        summary_text=formatted_summary,
        messages_replaced=replacement_count,
        tokens_removed=max(0, pre_tokens - post_tokens),
        pre_tokens=pre_tokens,
        post_tokens=post_tokens,
        preserve_messages_used=tail,
    )


def should_auto_compact(
    projected_input_tokens: int,
    auto_compact_threshold_tokens: int | None,
) -> bool:
    """判断当前投影 token 是否达到 auto compact 阈值。

    Args:
        projected_input_tokens (int): 当前轮次的投影输入 token 数。
        auto_compact_threshold_tokens (int | None): 配置中的 auto compact 阈值。

    Returns:
        bool: 达到或超过阈值时返回 True；阈值为 None 时返回 False。
    """
    if auto_compact_threshold_tokens is None:
        return False
    return projected_input_tokens >= max(0, auto_compact_threshold_tokens)


def is_context_length_error(exc: OpenAIClientError) -> bool:
    """判断异常是否属于 prompt/context length 类错误。

    Args:
        exc (OpenAIClientError): 待分类的客户端异常。

    Returns:
        bool: 当前异常是否应触发 reactive compact 分支。
    """
    if isinstance(exc, OpenAIResponseError):
        detail = f'{exc.detail} {exc}'.lower()
        if exc.status_code == 413:
            return True
    else:
        detail = str(exc).lower()

    keywords = (
        'context length',
        'context window',
        'maximum context length',
        'prompt too long',
        'prompt is too long',
        'too many tokens',
        'context_length_exceeded',
        'token limit exceeded',
    )
    return any(keyword in detail for keyword in keywords)


def _count_system_prefix(messages: list[JSONDict]) -> int:
    """返回头部连续 system 消息的数量。

    Args:
        messages (list[JSONDict]): 当前会话消息列表。

    Returns:
        int: 不参与 compact 的前缀 system 消息数量。
    """
    count = 0
    for message in messages:
        if message.get('role') == 'system':
            count += 1
        else:
            break
    return count


def _render_messages(messages: list[JSONDict]) -> str:
    """把消息列表渲染成供 compact 模型阅读的纯文本历史。

    Args:
        messages (list[JSONDict]): 待渲染的候选历史消息列表。

    Returns:
        str: 逐条带序号与附加元数据的文本表示。
    """
    parts: list[str] = []
    for index, message in enumerate(messages, start=1):
        role = str(message.get('role', 'unknown'))
        content = _normalize_content(message.get('content', ''))
        extras: list[str] = []

        if 'name' in message:
            extras.append(f"name={message['name']}")
        if 'tool_call_id' in message:
            extras.append(f"tool_call_id={message['tool_call_id']}")
        tool_calls = message.get('tool_calls')
        if tool_calls:
            extras.append(f'tool_calls={json.dumps(tool_calls, ensure_ascii=False)}')

        header = f'[{index}] role={role}'
        if extras:
            header = f"{header} ({', '.join(extras)})"
        parts.append(f'{header}\n{content or "(empty)"}')

    return '\n\n'.join(parts).strip()


def _normalize_content(content: object) -> str:
    """把消息 content 归一化为单个可读字符串。

    Args:
        content (object): 原始消息内容，可能是字符串、内容块列表或任意 JSON 值。

    Returns:
        str: 适合写入 compact prompt 的规范化文本。
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        rendered: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get('text')
                if isinstance(text, str):
                    rendered.append(text)
                else:
                    rendered.append(json.dumps(item, ensure_ascii=False))
            else:
                rendered.append(str(item))
        return '\n'.join(part.strip() for part in rendered if part is not None).strip()
    return json.dumps(content, ensure_ascii=False)