"""主动 compact 与 reactive compact 的上下文摘要压缩器。

本模块提供 Compactor，通过调用语言模型将旧对话历史压缩为摘要，并原地写回消息列表。
支持两种触发场景：auto-compact（投影超出阈值时主动触发）和 reactive compact（模型
返回 context length 错误后的恢复性重试）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .token_estimator import TokenEstimator
from core_contracts.context_contracts import CompactionResult
from core_contracts.model import ModelClient
from core_contracts.primitives import JSONDict


_COMPACT_BOUNDARY_PREFIX = (
    '<system-reminder>\nEarlier conversation history was compacted to save context.'
)
_COMPACT_SUMMARY_PREFIX = (
    '<system-reminder>\nCompact summary of earlier conversation:'
)
_COMPACT_PROMPT = (
    'You are compressing earlier conversation history for a coding agent. '
    'Return plain text only. Summarize the essential state needed to continue the task. '
    'Include: user goal, important files or tools already used, key findings or edits, '
    'and the next concrete step. Do not ask follow-up questions. Do not call tools.'
)


@dataclass
class Compactor:
    """负责主动 compact 与 reactive compact 的上下文摘要压缩器。

    核心工作流：
    1. compact() 构造压缩请求消息，调用模型，将摘要原地写回消息列表；
    2. should_auto_compact() 供调用方判断是否需要提前触发主动压缩；
    3. is_context_length_error() 供调用方识别 reactive compact 的触发条件。
    """

    client: ModelClient
    # ModelClient：用于生成摘要的模型客户端，由外部注入。

    token_estimator: TokenEstimator = field(default_factory=TokenEstimator)
    # ContextTokenEstimator：共享的启发式 token 估算器，用于统计 compact 前后变化。

    def compact(
        self,
        messages: list[JSONDict],
        *,
        preserve_messages: int = 4,
    ) -> CompactionResult:
        """调用模型生成摘要并把旧消息原地替换为 compact summary。

        Args:
            messages (list[JSONDict]): 当前会话消息列表（就地修改）。
            preserve_messages (int): 尾部保留不参与压缩的消息条数。
        Returns:
            CompactionResult: compact 执行结果，包含是否成功、摘要文本及 token 统计。
        Raises:
            无（模型调用异常被捕获并写入 CompactionResult.error）。
        """
        request_messages = self._build_request_messages(
            messages, preserve_messages=preserve_messages
        )
        if request_messages is None:
            return CompactionResult(compacted=False, error='Not enough messages to compact')

        try:
            response = self.client.complete(messages=request_messages, tools=[])
        except Exception as exc:
            return CompactionResult(compacted=False, error=str(exc))

        if response.tool_calls:
            return CompactionResult(
                compacted=False,
                usage=response.usage,
                error='Compact response unexpectedly requested tools',
            )

        summary = self._format_summary(response.content)
        if not summary:
            return CompactionResult(
                compacted=False,
                usage=response.usage,
                error='Compact model returned empty summary',
            )

        result = self._apply_summary(messages, summary, preserve_messages=preserve_messages)
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

    def _build_request_messages(
        self,
        messages: list[JSONDict],
        *,
        preserve_messages: int = 4,
    ) -> list[JSONDict] | None:
        """构造发送给 compact 模型的请求消息列表。

        Args:
            messages (list[JSONDict]): 完整的会话消息列表。
            preserve_messages (int): 尾部保留不参与压缩的消息条数。
        Returns:
            list[JSONDict] | None: 压缩请求消息；消息数不足时返回 None。
        Raises:
            无。
        """
        prefix = self._count_system_prefix(messages)
        total = len(messages)
        tail = min(max(preserve_messages, 0), max(total - prefix, 0))
        upper = total - tail

        if upper <= prefix:
            return None

        rendered_history = self._render_messages(messages[prefix:upper])
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
        """返回头部连续 system 消息的数量（不参与压缩范围）。

        Args:
            messages (list[JSONDict]): 完整消息列表。
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

    def _render_messages(self, messages: list[JSONDict]) -> str:
        """把消息列表渲染为供 compact 模型阅读的纯文本历史记录。

        Args:
            messages (list[JSONDict]): 待渲染的消息子列表。
        Returns:
            str: 多段落纯文本历史；若所有消息均为空则返回空字符串。
        Raises:
            无。
        """
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
        """将消息 content 字段归一化为单个可读字符串。

        Args:
            content (object): 消息的 content 字段，可能为字符串、列表或其他对象。
        Returns:
            str: 归一化后的纯文本内容；列表型内容按块拼接，其他对象 JSON 序列化。
        Raises:
            无。
        """
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    parts.append(json.dumps(item, ensure_ascii=False))
                    continue
                if item.get('type') == 'text':
                    parts.append(str(item.get('text', '')).strip())
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            return '\n'.join(part for part in parts if part).strip()
        return json.dumps(content, ensure_ascii=False).strip()

    def _format_summary(self, content: str) -> str:
        """清洗并规范化模型返回的摘要文本。

        Args:
            content (str): 模型返回的原始摘要内容。
        Returns:
            str: 清洗后的摘要文本（连续空行折叠为一行）；内容为空时返回空字符串。
        Raises:
            无。
        """
        normalized = re.sub(r'\n{3,}', '\n\n', str(content or '').strip())
        return normalized.strip()

    def _apply_summary(
        self,
        messages: list[JSONDict],
        summary: str,
        *,
        preserve_messages: int,
    ) -> CompactionResult:
        """将摘要原地写回消息列表并统计 token 变化。

        Args:
            messages (list[JSONDict]): 需要就地修改的完整消息列表。
            summary (str): 已清洗的摘要文本。
            preserve_messages (int): 尾部保留不替换的消息条数。
        Returns:
            CompactionResult: 写回结果，含 token 前后变化与替换条数（不含模型用量）。
        Raises:
            无。
        """
        prefix = self._count_system_prefix(messages)
        total = len(messages)
        tail = min(max(preserve_messages, 0), max(total - prefix, 0))
        upper = total - tail

        if upper <= prefix:
            return CompactionResult(compacted=False, error='Not enough messages to compact')

        pre_tokens = self.token_estimator.estimate_messages(messages)
        messages_replaced = upper - prefix
        preserved_tail = list(messages[upper:])
        replacement = [
            {'role': 'system', 'content': _COMPACT_BOUNDARY_PREFIX},
            {'role': 'system', 'content': f'{_COMPACT_SUMMARY_PREFIX}\n{summary}'},
        ]
        messages[:] = [*messages[:prefix], *replacement, *preserved_tail]
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

    def should_auto_compact(
        self,
        projected_input_tokens: int,
        auto_compact_threshold_tokens: int | None,
    ) -> bool:
        """判断当前投影 token 是否达到 auto compact 阈值。

        Args:
            projected_input_tokens (int): 本次调用预估消耗的输入 token 总量。
            auto_compact_threshold_tokens (int | None): auto compact 触发阈值；None 表示禁用。
        Returns:
            bool: True 表示已达阈值、需要触发 compact；False 表示无需操作。
        Raises:
            无。
        """
        if auto_compact_threshold_tokens is None:
            return False
        return projected_input_tokens >= max(0, auto_compact_threshold_tokens)

    def is_context_length_error(self, exc: Exception) -> bool:
        """判断异常是否属于 prompt/context length 类错误。

        Args:
            exc (Exception): 模型调用抛出的异常对象。
        Returns:
            bool: True 表示该异常由上下文长度超限引起，可尝试 reactive compact 恢复。
        Raises:
            无。
        """
        if getattr(exc, 'status_code', None) == 413:
            return True

        detail_text = str(getattr(exc, 'detail', ''))
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

