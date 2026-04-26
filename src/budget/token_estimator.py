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
    """按统一启发式规则估算消息与工具定义的输入 token。"""

    chars_per_token: int = _CHARS_PER_TOKEN
    message_overhead_tokens: int = _MSG_OVERHEAD
    chat_base_tokens: int = _CHAT_BASE

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        return self.chat_base_tokens + sum(self.estimate_message(message) for message in messages)

    def estimate_message(self, message: dict[str, Any]) -> int:
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
        if not tools:
            return 0
        serialized = json.dumps(tools, ensure_ascii=False)
        return self._estimate_text_tokens(serialized)

    def _estimate_text_tokens(self, text: str) -> int:
        return max(1, (len(text) + self.chars_per_token - 1) // self.chars_per_token)