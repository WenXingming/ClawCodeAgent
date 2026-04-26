"""ISSUE-009 Token 估算器。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


_CHARS_PER_TOKEN: int = 4
_MSG_OVERHEAD: int = 4
_CHAT_BASE: int = 3


@dataclass(frozen=True)
class ContextTokenEstimator:
    """按统一启发式规则估算消息与工具定义的输入 token。
    
    使用字符长度启发式（默认每4字符≈1token）快速估算，
    无需真实编码，适用于预算预检场景。
    """

    chars_per_token: int = _CHARS_PER_TOKEN  # 启发式：每N个字符≈1个token
    message_overhead_tokens: int = _MSG_OVERHEAD  # 每条消息的固定开销token数
    chat_base_tokens: int = _CHAT_BASE  # 消息列表的基础token数

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表的总输入 token 数。
        
        对列表中每条消息调用estimate_message，累加结果后加上基础token。
        
        Args:
            messages (list[dict[str, Any]]): 消息列表，每项为{"role": "...", "content": "..."}
            
        Returns:
            int: 估算的总 token 数（>=基础token值）
        """
        return self.chat_base_tokens + sum(self.estimate_message(message) for message in messages)

    def estimate_message(self, message: dict[str, Any]) -> int:
        """估算单条消息的 token 数。
        
        支持content为字符串、列表（多模态）或其他类型，
        逐项计算后加上role和消息开销token。
        
        Args:
            message (dict[str, Any]): 单条消息，应包含'role'和'content'字段
            
        Returns:
            int: 估算的 token 数
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
        
        将工具列表序列化为JSON，再按字符长度估算。
        
        Args:
            tools (list[dict[str, Any]]): 工具定义列表
            
        Returns:
            int: 估算的 token 数；若工具列表为空则返回0
        """
        if not tools:
            return 0
        serialized = json.dumps(tools, ensure_ascii=False)
        return self._estimate_text_tokens(serialized)

    def _estimate_text_tokens(self, text: str) -> int:
        """内部方法：按字符长度启发式估算文本 token 数。
        
        使用向上取整公式保证至少返回1。
        
        Args:
            text (str): 待估算的文本
            
        Returns:
            int: 估算的 token 数（最小值1）
        """
        return max(1, (len(text) + self.chars_per_token - 1) // self.chars_per_token)