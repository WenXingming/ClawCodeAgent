"""提供统一的上下文 token 启发式估算能力。

本模块不依赖真实 tokenizer，而是用统一的字符长度启发式快速估算消息与工具定义的 token 开销，供预算预检、snip 与 compact 等流程共享，保证整条上下文治理链路的统计口径一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


_CHARS_PER_TOKEN: int = 4  # 默认按每 4 个字符近似 1 个 token。
_MSG_OVERHEAD: int = 4  # 每条消息额外附带的固定协议开销。
_CHAT_BASE: int = 3  # 整个聊天请求的基础固定开销。


@dataclass(frozen=True)
class ContextTokenEstimator:
    """按统一启发式规则估算消息与工具定义的输入 token。

    典型用法如下：
    1. `estimate_messages()` 估算整段消息上下文。
    2. `estimate_tools()` 估算工具定义带来的附加输入开销。
    3. `estimate_message()` 供 snip、compact 等局部流程比较单条消息改写前后的成本差异。
    """

    chars_per_token: int = _CHARS_PER_TOKEN  # int：启发式下每多少字符近似等于 1 个 token。
    message_overhead_tokens: int = _MSG_OVERHEAD  # int：每条消息的固定开销 token 数。
    chat_base_tokens: int = _CHAT_BASE  # int：整个消息列表的基础 token 数。

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表的总输入 token 数。

        对列表中每条消息调用 `estimate_message()`，累加结果后再加上聊天级基础开销。

        Args:
            messages (list[dict[str, Any]]): 待估算的消息列表。
        Returns:
            int: 估算的总 token 数，结果至少包含聊天级基础开销。
        """
        return self.chat_base_tokens + sum(self.estimate_message(message) for message in messages)

    def estimate_message(self, message: dict[str, Any]) -> int:
        """估算单条消息的 token 数。

        支持 `content` 为字符串、内容块列表或其他可 JSON 序列化对象，并统一折算为启发式 token 数。

        Args:
            message (dict[str, Any]): 单条消息对象，通常包含 `role` 与 `content` 字段。
        Returns:
            int: 该消息的估算 token 数。
        """
        content = message.get('content', '')
        if isinstance(content, str):
            content_tokens = self._estimate_text_tokens(content) if content else 0
        elif isinstance(content, list):
            content_tokens = 0
            for block in content:
                if isinstance(block, dict):
                    text = block.get('text', '')
                    if isinstance(text, str) and text:
                        content_tokens += self._estimate_text_tokens(text)
                    else:
                        content_tokens += self._estimate_text_tokens(json.dumps(block, ensure_ascii=False))
                else:
                    content_tokens += self._estimate_text_tokens(str(block))
        else:
            content_tokens = self._estimate_text_tokens(json.dumps(content, ensure_ascii=False))

        role_tokens = self._estimate_text_tokens(str(message.get('role', '')))
        return role_tokens + content_tokens + self.message_overhead_tokens

    def estimate_tools(self, tools: list[dict[str, Any]]) -> int:
        """估算工具定义列表的 token 数。

        将工具列表序列化为 JSON，再按字符长度启发式估算。

        Args:
            tools (list[dict[str, Any]]): 当前可提供给模型的工具定义列表。
        Returns:
            int: 估算的 token 数；若工具列表为空则返回 0。
        """
        if not tools:
            return 0
        serialized = json.dumps(tools, ensure_ascii=False)
        return self._estimate_text_tokens(serialized)

    def _estimate_text_tokens(self, text: str) -> int:
        """内部方法：按字符长度启发式估算文本 token 数。

        使用向上取整公式保证非空文本至少返回 1。

        Args:
            text (str): 待估算的文本。
        Returns:
            int: 估算的 token 数，最小值为 1。
        """
        return max(1, (len(text) + self.chars_per_token - 1) // self.chars_per_token)
