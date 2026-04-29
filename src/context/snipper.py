"""轻量级上下文剪裁器（tombstone 化）。

本模块提供 Snipper，将旧消息替换为占位 tombstone 摘要，在不丢失对话结构的前提下
大幅降低上下文 token 开销。操作就地修改消息列表，并返回 SnipResult 统计信息。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .token_estimator import TokenEstimator
from core_contracts.context_contracts import SnipResult


@dataclass
class Snipper:
    """管理旧消息 tombstone 化的上下文轻量剪裁器。

    核心工作流：
    1. snip() 接收消息列表，跳过头部 system 前缀与尾部保留窗口；
    2. 对中间段中所有可剪裁消息生成 tombstone 替代内容并就地写回；
    3. 累计 token 差值后返回 SnipResult 统计快照。
    """

    long_assistant_threshold: int = 300
    # int：assistant 消息中超过此字符数才被视为"长文本"，进而允许被剪裁。

    preview_max_chars: int = 120
    # int：tombstone 中 preview 文本的最大字符数，超出后截断并追加省略号。

    token_estimator: TokenEstimator = field(default_factory=TokenEstimator)
    # ContextTokenEstimator：共享的启发式 token 估算器，用于统计节省量。

    def snip(
        self,
        messages: list[dict[str, Any]],
        *,
        preserve_messages: int = 4,
        tools: list[dict[str, Any]] | None = None,
        max_input_tokens: int | None = None,
    ) -> SnipResult:
        """就地剪裁消息列表中的旧候选消息并返回统计结果。

        Args:
            messages (list[dict[str, Any]]): 待剪裁的消息列表（就地修改）。
            preserve_messages (int): 尾部保留的消息条数，不参与剪裁。
            tools (list[dict[str, Any]] | None): 保留参数，当前实现未使用。
            max_input_tokens (int | None): 保留参数，当前实现未使用。
        Returns:
            SnipResult: 本次剪裁的统计快照，含 snipped_count 与 tokens_removed。
        Raises:
            无。
        """
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
        """返回头部连续 system 消息的数量（不参与剪裁范围）。

        Args:
            messages (list[dict[str, Any]]): 完整消息列表。
        Returns:
            int: 头部连续 system 消息的条数。
        Raises:
            无。
        """
        count = 0
        for message in messages:
            if message.get('role') == 'system':
                count += 1
            else:
                break
        return count

    def _is_snippable(self, message: dict[str, Any]) -> bool:
        """判断单条消息是否满足剪裁条件。

        tombstone 消息、system 消息和 user 消息不可剪裁；
        tool 消息和带 tool_calls 的 assistant 消息或超长 assistant 消息可剪裁。

        Args:
            message (dict[str, Any]): 待判断的消息对象。
        Returns:
            bool: True 表示该消息可被 tombstone 化，False 表示需要保留原内容。
        Raises:
            无。
        """
        content = message.get('content', '')
        if isinstance(content, str) and content.startswith('<system-reminder>\nOlder '):
            return False

        role = message.get('role', '')
        if role == 'tool':
            return True

        if role == 'assistant':
            if message.get('tool_calls'):
                return True
            text = content if isinstance(content, str) else json.dumps(content)
            if len(text) > self.long_assistant_threshold:
                return True

        return False

    def _make_tombstone(self, message: dict[str, Any]) -> dict[str, Any]:
        """为单条消息生成 tombstone 替代内容。

        Args:
            message (dict[str, Any]): 原始待替换消息。
        Returns:
            dict[str, Any]: 携带 tombstone 摘要的替代消息，保留 role / tool_call_id / name 等字段。
        Raises:
            无。
        """
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
        """将原始内容折叠为可写入 tombstone 的短预览文本。

        Args:
            content (object): 消息的 content 字段，可以是字符串或任意可序列化对象。
        Returns:
            str: 折叠后的预览文本；超过 preview_max_chars 时截断并追加省略号。
        Raises:
            无。
        """
        if isinstance(content, str):
            text = ' '.join(content.split())
        else:
            text = ' '.join(json.dumps(content).split())

        if len(text) > self.preview_max_chars:
            return text[: self.preview_max_chars - 3] + '...'
        return text
