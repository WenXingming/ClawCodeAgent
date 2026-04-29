"""统一的上下文 token 启发式估算器。

本模块提供 ContextTokenEstimator，供 budget_projection、snipper、compactor 三条链路
共享同一套字符长度启发式估算规则，确保整个上下文治理链路的统计口径严格一致。
不依赖真实 tokenizer，以最低开销完成 pre-model 预算预检。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


_CHARS_PER_TOKEN: int = 4   # 默认按每 4 个字符近似 1 个 token。
_MSG_OVERHEAD: int = 4      # 每条消息额外附带的固定协议开销（role 分隔符等）。
_CHAT_BASE: int = 3         # 整个聊天请求的基础固定开销（请求结构固定字段）。


@dataclass(frozen=True)
class TokenEstimator:
    """按统一启发式规则估算消息与工具定义的输入 token 数。

    核心工作流：
    1. estimate_messages() 估算整段会话上下文的总 token 量；
    2. estimate_message()  供 snip / compact 比较单条消息改写前后的成本差异；
    3. estimate_tools()    估算工具 schema 带来的附加 token 开销。
    """

    chars_per_token: int = _CHARS_PER_TOKEN         # int：启发式下每多少字符近似等于 1 个 token。
    message_overhead_tokens: int = _MSG_OVERHEAD    # int：每条消息的固定开销 token 数。
    chat_base_tokens: int = _CHAT_BASE              # int：整个消息列表的基础固定 token 数。

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表的总输入 token 数。

        对列表中每条消息调用 estimate_message()，累加后加上聊天级基础开销。

        Args:
            messages (list[dict[str, Any]]): 待估算的消息列表。
        Returns:
            int: 估算的总 token 数，最小值为 chat_base_tokens。
        Raises:
            无。
        """
        return self.chat_base_tokens + sum(
            self.estimate_message(message) for message in messages
        )

    def estimate_message(self, message: dict[str, Any]) -> int:
        """估算单条消息的 token 数。

        支持 content 为字符串、内容块列表或任意可 JSON 序列化对象，统一折算为启发式 token。

        Args:
            message (dict[str, Any]): 单条消息对象，通常包含 role 与 content 字段。
        Returns:
            int: 该消息的估算 token 数（至少为 message_overhead_tokens）。
        Raises:
            无。
        """
        content = message.get('content', '')
        role_tokens = self._estimate_text_tokens(str(message.get('role', '')))

        if isinstance(content, str):
            content_tokens = self._estimate_text_tokens(content) if content else 0
        elif isinstance(content, list):
            content_tokens = self._estimate_content_block_tokens(content)
        else:
            content_tokens = self._estimate_text_tokens(
                json.dumps(content, ensure_ascii=False)
            )

        return role_tokens + content_tokens + self.message_overhead_tokens

    def _estimate_content_block_tokens(self, blocks: list[Any]) -> int:
        """估算内容块列表（multimodal content）的 token 数。

        Args:
            blocks (list[Any]): content 字段为列表时的内容块集合。
        Returns:
            int: 所有内容块合计估算 token 数。
        Raises:
            无。
        """
        total = 0
        for block in blocks:
            if isinstance(block, dict):
                text = block.get('text', '')
                if isinstance(text, str) and text:
                    total += self._estimate_text_tokens(text)
                else:
                    total += self._estimate_text_tokens(
                        json.dumps(block, ensure_ascii=False)
                    )
            else:
                total += self._estimate_text_tokens(str(block))
        return total

    def estimate_tools(self, tools: list[dict[str, Any]]) -> int:
        """估算工具定义列表的 token 数。

        将工具列表序列化为 JSON 后按字符长度启发式估算。

        Args:
            tools (list[dict[str, Any]]): 当前可提供给模型的工具定义列表。
        Returns:
            int: 估算的 token 数；工具列表为空则返回 0。
        Raises:
            无。
        """
        if not tools:
            return 0
        return self._estimate_text_tokens(json.dumps(tools, ensure_ascii=False))

    def _estimate_text_tokens(self, text: str) -> int:
        """按字符长度启发式估算文本 token 数（向上取整，最小值为 1）。

        Args:
            text (str): 待估算的文本。
        Returns:
            int: 估算的 token 数，最小值为 1。
        Raises:
            无。
        """
        return max(1, (len(text) + self.chars_per_token - 1) // self.chars_per_token)

