"""ISSUE-011 Compact 与 Reactive Compact：摘要压缩旧消息以释放上下文预算。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..core_contracts import JSONDict, TokenUsage
from ..openai_client.openai_client import OpenAIClient, OpenAIClientError, OpenAIResponseError
from .token_budget import estimate_messages_tokens

_COMPACT_BOUNDARY_PREFIX = '<system-reminder>\nEarlier conversation history was compacted to save context.'
_COMPACT_SUMMARY_PREFIX = '<system-reminder>\nCompact summary of earlier conversation:'

_COMPACT_PROMPT = (
    'You are compressing earlier conversation history for a coding agent. '\
    'Return plain text only. Summarize the essential state needed to continue the task. '\
    'Include: user goal, important files or tools already used, key findings or edits, '\
    'and the next concrete step. Do not ask follow-up questions. Do not call tools.'
)


@dataclass(frozen=True)
class CompactResult:
    """compact_conversation 的结果。"""

    compacted: bool
    summary_text: str = ''
    messages_replaced: int = 0
    tokens_removed: int = 0
    pre_tokens: int = 0
    post_tokens: int = 0
    preserve_messages_used: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    error: str | None = None


def should_auto_compact(
    projected_input_tokens: int,
    auto_compact_threshold_tokens: int | None,
) -> bool:
    """判断当前投影 token 是否达到 auto compact 阈值。"""
    if auto_compact_threshold_tokens is None:
        return False
    return projected_input_tokens >= max(0, auto_compact_threshold_tokens)


def is_context_length_error(exc: OpenAIClientError) -> bool:
    """判断异常是否表示 prompt/context length 类错误。"""
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


def build_compact_request_messages(
    messages: list[JSONDict],
    *,
    preserve_messages: int = 4,
) -> list[JSONDict] | None:
    """构造发送给模型的 compact 请求消息。"""
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
    """清理 compact 返回的摘要文本，避免保存噪声格式。"""
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
    """把中间段消息替换为 compact boundary + summary，原地修改列表。"""
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
        'content': (
            '<system-reminder>\n'
            'Earlier conversation history was compacted to save context.\n'
            '</system-reminder>'
        ),
    }
    summary_message = {
        'role': 'system',
        'content': (
            '<system-reminder>\n'
            'Compact summary of earlier conversation:\n'
            f'{formatted_summary}\n'
            '</system-reminder>'
        ),
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


def compact_conversation(
    client: OpenAIClient,
    messages: list[JSONDict],
    *,
    preserve_messages: int = 4,
) -> CompactResult:
    """调用模型生成摘要，并将 messages 原地压缩为 summary 形式。"""
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


def _count_system_prefix(messages: list[JSONDict]) -> int:
    count = 0
    for message in messages:
        if message.get('role') == 'system':
            count += 1
        else:
            break
    return count


def _render_messages(messages: list[JSONDict]) -> str:
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