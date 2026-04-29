"""执行轻量级上下文剪裁，把旧消息替换为 tombstone 提示。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from context.context_token_estimator import ContextTokenEstimator


@dataclass(frozen=True)
class SnipResult:
    """描述一次 snip 操作的统计结果。"""

    snipped_count: int
    tokens_removed: int


@dataclass
class Snipper:
    """管理旧消息 tombstone 化的上下文剪裁器。"""

    long_assistant_threshold: int = 300
    preview_max_chars: int = 120
    token_estimator: ContextTokenEstimator = field(default_factory=ContextTokenEstimator)

    def snip(
        self,
        messages: list[dict[str, Any]],
        *,
        preserve_messages: int = 4,
        tools: list[dict[str, Any]] | None = None,
        max_input_tokens: int | None = None,
    ) -> SnipResult:
        """就地剪裁消息列表中的旧候选消息。"""
        del tools, max_input_tokens

        prefix = self._count_prefix(messages)
        total = len(messages)
        tail = min(max(preserve_messages, 0), max(total - prefix, 0))
        upper = total - tail

        snipped_count = 0
        tokens_removed = 0

        for index in range(prefix, upper):
            message = messages[index]
            if not self._is_snippable(message):
                continue
            original_tokens = self.token_estimator.estimate_message(message)
            tombstone = self._make_tombstone(message)
            tombstone_tokens = self.token_estimator.estimate_message(tombstone)
            messages[index] = tombstone
            snipped_count += 1
            tokens_removed += max(0, original_tokens - tombstone_tokens)

        return SnipResult(snipped_count=snipped_count, tokens_removed=tokens_removed)

    def _count_prefix(self, messages: list[dict[str, Any]]) -> int:
        """返回头部连续 system 消息的数量。"""
        count = 0
        for message in messages:
            if message.get('role') == 'system':
                count += 1
            else:
                break
        return count

    def _is_snippable(self, message: dict[str, Any]) -> bool:
        """判断单条消息是否允许被剪裁。"""
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
            text = content if isinstance(content, str) else json.dumps(content)
            if len(text) > self.long_assistant_threshold:
                return True

        return False

    def _make_tombstone(self, message: dict[str, Any]) -> dict[str, Any]:
        """为单条消息生成 tombstone 替代内容。"""
        role = message.get('role', '')
        preview_text = self._build_preview(message.get('content', ''))
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
            f'Preview: {preview_text or "(empty)"}\n'
            f'</system-reminder>'
        )

        result: dict[str, Any] = {'role': role, 'content': tombstone_content}
        if role == 'tool':
            if 'tool_call_id' in message:
                result['tool_call_id'] = message['tool_call_id']
            if 'name' in message:
                result['name'] = message['name']
        elif role == 'assistant' and tool_calls:
            result['tool_calls'] = tool_calls

        return result

    def _build_preview(self, content: object) -> str:
        """把原始内容折叠为可写入 tombstone 的短预览。"""
        if isinstance(content, str):
            text = ' '.join(content.split())
        else:
            text = ' '.join(json.dumps(content).split())
        if len(text) > self.preview_max_chars:
            return text[: self.preview_max_chars - 3] + '...'
        return text