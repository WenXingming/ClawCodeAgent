"""核心基础类型与 token 使用统计契约。

定义 JSONDict 全局类型别名与 TokenUsage 计费统计对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._coercion import _as_dict, _as_int, _first_present

JSONDict = dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    """一次或累计模型调用的 token 使用统计。

    该对象可独立表达增量或累计用量，支持加法合并与成本核算。
    """

    input_tokens: int = 0  # int：普通输入 token 数量。
    output_tokens: int = 0  # int：普通输出 token 数量。
    cache_creation_input_tokens: int = 0  # int：缓存写入输入 token 数量。
    cache_read_input_tokens: int = 0  # int：缓存命中输入 token 数量。
    reasoning_tokens: int = 0  # int：推理 token 数量。

    @property
    def total_tokens(self) -> int:
        """返回输入、输出与缓存 token 的总和（不含 reasoning token）。
        Returns:
            int: 四种主要 token 维度的总和。
        """
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """将两个 TokenUsage 按字段累加，返回新实例。
        Args:
            other (TokenUsage): 待累加的另一个 TokenUsage。
        Returns:
            TokenUsage: 字段逐一相加后的新对象。
        """
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_dict(self) -> JSONDict:
        """把 token 统计转换为 JSON 字典。
        Returns:
            JSONDict: 包含全部字段的可序列化字典。
        """
        return {
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cache_creation_input_tokens': self.cache_creation_input_tokens,
            'cache_read_input_tokens': self.cache_read_input_tokens,
            'reasoning_tokens': self.reasoning_tokens,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'TokenUsage':
        """从 JSON 字典恢复 TokenUsage，兼容 snake_case 与 camelCase 字段别名。
        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            TokenUsage: 恢复后的对象。
        """
        data = _as_dict(payload)
        return cls(
            input_tokens=_as_int(
                _first_present(data, 'input_tokens', 'inputTokens', 'prompt_tokens', 'promptTokens', default=0),
                0,
            ),
            output_tokens=_as_int(
                _first_present(data, 'output_tokens', 'outputTokens', 'completion_tokens', 'completionTokens', default=0),
                0,
            ),
            cache_creation_input_tokens=_as_int(
                _first_present(
                    data,
                    'cache_creation_input_tokens',
                    'cacheCreationInputTokens',
                    'cache_creation',
                    default=0,
                ),
                0,
            ),
            cache_read_input_tokens=_as_int(
                _first_present(data, 'cache_read_input_tokens', 'cacheReadInputTokens', default=0),
                0,
            ),
            reasoning_tokens=_as_int(
                _first_present(data, 'reasoning_tokens', 'reasoningTokens', default=0),
                0,
            ),
        )
