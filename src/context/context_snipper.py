"""ISSUE-010 Snip 上下文剪裁：把旧消息原地替换为 tombstone 摘要。

本模块负责在 `is_soft_over=True` 时做轻量级上下文瘦身。它不会压缩语义，只会把
可恢复的旧消息内容替换成短摘要，以降低 prompt 压力并尽量保持消息链结构不变。

文件内定义按“公共对象优先，再顺着第一次调用链往下读”的顺序组织。当前主阅读链为：

`ContextSnipper.snip()`
-> `_count_prefix()`
-> `_is_snippable()`
-> `_make_tombstone()`
-> `_build_preview()`
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from budget.token_estimator import ContextTokenEstimator

@dataclass(frozen=True)
class SnipResult:
    """描述一次 snip 操作的统计结果。"""

    snipped_count: int  # int：本次被 tombstone 替换掉的消息数量。
    tokens_removed: int  # int：本次估算节省的 token 数。

@dataclass
class ContextSnipper:
    """管理旧消息 tombstone 化的上下文剪裁器。

    典型工作流如下：
    1. runtime 在 token 预检后发现 `snapshot.is_soft_over=True`。
    2. 调用 `snip()`，仅处理前缀 system 与尾部保留区之间的中间旧消息。
    3. 对可剪裁消息生成 tombstone，并返回本轮节省的 token 统计。
    """

    long_assistant_threshold: int = 300  # int：assistant 文本超过该阈值后才可作为长输出候选。
    preview_max_chars: int = 120  # int：tombstone 预览文本允许保留的最大字符数。
    token_estimator: ContextTokenEstimator = field(default_factory=ContextTokenEstimator)  # ContextTokenEstimator：用于比较原消息与 tombstone 的 token 差值。

    def snip(
        self,
        messages: list[dict[str, Any]],
        *,
        preserve_messages: int = 4,
        tools: list[dict[str, Any]] | None = None,
        max_input_tokens: int | None = None,
    ) -> SnipResult:
        """就地剪裁消息列表中的旧候选消息。

        Args:
            messages (list[dict[str, Any]]): `AgentSessionState.messages` 的直接引用，会被原地修改。
            preserve_messages (int): 尾部保留不剪裁的消息数量。
            tools (list[dict[str, Any]] | None): 当前 openai tools 定义列表；预留给未来策略扩展。
            max_input_tokens (int | None): 最大输入 token 上限；预留给未来策略扩展。

        Returns:
            SnipResult: 本次剪裁统计；`snipped_count=0` 表示无变化。
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
        """返回头部连续 system 消息的数量。

        Args:
            messages (list[dict[str, Any]]): 当前会话消息列表。

        Returns:
            int: 头部连续 system 消息的数量，也就是不可剪裁前缀的长度。
        """
        count = 0
        for message in messages:
            if message.get('role') == 'system':
                count += 1
            else:
                break
        return count

    def _is_snippable(self, message: dict[str, Any]) -> bool:
        """判断单条消息是否允许被剪裁。

        Args:
            message (dict[str, Any]): 待判定的消息对象。

        Returns:
            bool: 当前消息是否可以被 tombstone 替换。
        """
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
        """为单条消息生成 tombstone 替代内容。

        Args:
            message (dict[str, Any]): 原始消息对象。

        Returns:
            dict[str, Any]: 保留必要协议字段后的 tombstone 消息对象。
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
        """把原始内容折叠为可写入 tombstone 的短预览。

        Args:
            content (object): 原始消息内容，可能是字符串或任意可 JSON 序列化值。

        Returns:
            str: 去空白并按上限截断后的预览文本。
        """
        if isinstance(content, str):
            text = ' '.join(content.split())
        else:
            text = ' '.join(json.dumps(content).split())
        if len(text) > self.preview_max_chars:
            return text[: self.preview_max_chars - 3] + '...'
        return text
