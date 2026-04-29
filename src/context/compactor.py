"""执行主动 compact 与 reactive compact 所需的上下文摘要压缩。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from context.context_token_estimator import ContextTokenEstimator
from core_contracts.openai_contracts import ModelClient
from core_contracts.protocol import JSONDict
from core_contracts.token_usage import TokenUsage

_COMPACT_BOUNDARY_PREFIX = '<system-reminder>\nEarlier conversation history was compacted to save context.'
_COMPACT_SUMMARY_PREFIX = '<system-reminder>\nCompact summary of earlier conversation:'
_COMPACT_PROMPT = (
    'You are compressing earlier conversation history for a coding agent. '
    'Return plain text only. Summarize the essential state needed to continue the task. '
    'Include: user goal, important files or tools already used, key findings or edits, '
    'and the next concrete step. Do not ask follow-up questions. Do not call tools.'
)


@dataclass(frozen=True)
class CompactionResult:
    """描述一次 compact 操作的结果。"""

    compacted: bool
    summary_text: str = ''
    messages_replaced: int = 0
    tokens_removed: int = 0
    pre_tokens: int = 0
    post_tokens: int = 0
    preserve_messages_used: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    error: str | None = None


@dataclass
class Compactor:
    """负责主动 compact 与 reactive compact 的上下文压缩器。"""

    client: ModelClient
    token_estimator: ContextTokenEstimator = field(default_factory=ContextTokenEstimator)

    def compact(
        self,
        messages: list[JSONDict],
        *,
        preserve_messages: int = 4,
    ) -> CompactionResult:
        """调用模型生成摘要，并把旧消息原地替换为 compact summary。"""
        request_messages = self._build_request_messages(messages, preserve_messages=preserve_messages)
        if request_messages is None:
            return CompactionResult(compacted=False, error='Not enough messages to compact')

        try:
            response = self.client.complete(messages=request_messages, tools=[])
        except Exception as exc:
            return CompactionResult(compacted=False, error=str(exc))

        if response.tool_calls:
            return CompactionResult(compacted=False, error='Compact response unexpectedly requested tools')

        summary = self._format_summary(response.content)
        if not summary:
            return CompactionResult(
                compacted=False,
                usage=response.usage,
                error='Compact model returned empty summary',
            )

        result = self._apply_summary(
            messages,
            summary,
            preserve_messages=preserve_messages,
        )
        return CompactionResult(
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

    def should_auto_compact(
        self,
        projected_input_tokens: int,
        auto_compact_threshold_tokens: int | None,
    ) -> bool:
        """判断当前投影 token 是否达到 auto compact 阈值。"""
        if auto_compact_threshold_tokens is None:
            return False
        return projected_input_tokens >= max(0, auto_compact_threshold_tokens)

    def is_context_length_error(self, exc: Exception) -> bool:
        """判断异常是否属于 prompt/context length 类错误。"""
        status_code = getattr(exc, 'status_code', None)
        detail_text = str(getattr(exc, 'detail', ''))
        if status_code == 413:
            return True

        detail = f'{detail_text} {exc}'.lower()

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

    def _build_request_messages(
        self,
        messages: list[JSONDict],
        *,
        preserve_messages: int = 4,
    ) -> list[JSONDict] | None:
        """构造发送给 compact 模型的请求消息。"""
        prefix = self._count_system_prefix(messages)
        total = len(messages)
        tail = min(max(preserve_messages, 0), max(total - prefix, 0))
        upper = total - tail

        if upper <= prefix:
            return None

        candidate_messages = messages[prefix:upper]
        rendered_history = self._render_messages(candidate_messages)
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

    def _count_system_prefix(self, messages: list[JSONDict]) -> int:
        """返回头部连续 system 消息的数量。"""
        count = 0
        for message in messages:
            if message.get('role') == 'system':
                count += 1
            else:
                break
        return count

    def _render_messages(self, messages: list[JSONDict]) -> str:
        """把消息列表渲染成供 compact 模型阅读的纯文本历史。"""
        parts: list[str] = []
        for index, message in enumerate(messages, start=1):
            role = str(message.get('role', 'unknown'))
            content = self._normalize_content(message.get('content', ''))
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

    def _normalize_content(self, content: object) -> str:
        """把消息 content 归一化为单个可读字符串。"""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            normalized_parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    normalized_parts.append(item)
                    continue
                if not isinstance(item, dict):
                    normalized_parts.append(json.dumps(item, ensure_ascii=False))
                    continue
                item_type = item.get('type')
                if item_type == 'text':
                    normalized_parts.append(str(item.get('text', '')).strip())
                else:
                    normalized_parts.append(json.dumps(item, ensure_ascii=False))
            return '\n'.join(part for part in normalized_parts if part).strip()
        return json.dumps(content, ensure_ascii=False).strip()

    def _format_summary(self, content: str) -> str:
        """清洗模型返回的摘要文本。"""
        normalized = re.sub(r'\n{3,}', '\n\n', str(content or '').strip())
        return normalized.strip()

    def _apply_summary(
        self,
        messages: list[JSONDict],
        summary: str,
        *,
        preserve_messages: int,
    ) -> CompactionResult:
        """把摘要原地写回消息列表，并记录 token 变化。"""
        prefix = self._count_system_prefix(messages)
        total = len(messages)
        tail = min(max(preserve_messages, 0), max(total - prefix, 0))
        upper = total - tail
        if upper <= prefix:
            return CompactionResult(compacted=False, error='Not enough messages to compact')

        pre_tokens = self.token_estimator.estimate_messages(messages)
        messages_replaced = upper - prefix
        preserved_tail = list(messages[upper:])
        replacement_messages = [
            {'role': 'system', 'content': _COMPACT_BOUNDARY_PREFIX},
            {'role': 'system', 'content': f'{_COMPACT_SUMMARY_PREFIX}\n{summary}'},
        ]
        messages[:] = [*messages[:prefix], *replacement_messages, *preserved_tail]
        post_tokens = self.token_estimator.estimate_messages(messages)

        return CompactionResult(
            compacted=True,
            summary_text=summary,
            messages_replaced=messages_replaced,
            tokens_removed=max(0, pre_tokens - post_tokens),
            pre_tokens=pre_tokens,
            post_tokens=post_tokens,
            preserve_messages_used=tail,
        )