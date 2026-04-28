"""定义模型 token 使用统计的稳定契约。

本模块负责描述一次或多次模型调用累计产生的 token 使用数据，并提供统一的求和、序列化与反序列化入口。该契约会被 openai_client、session、run_result 与预算相关模块共同使用。
"""

from __future__ import annotations

from dataclasses import dataclass

from .coercion import _as_dict, _as_int, _first_present
from .protocol import JSONDict


@dataclass(frozen=True)
class TokenUsage:
    """模型调用产生的 token 使用统计。

    该对象用于记录一次或多次模型调用的 token 消耗详情，包括输入、输出、缓存等维度。外部通常通过 `to_dict()` / `from_dict()` 在 JSON 载荷和内存对象之间转换，并通过 `__add__()` 累加多次调用的统计结果。
    """

    input_tokens: int = 0  # int：本次统计中的输入 token 数。
    output_tokens: int = 0  # int：本次统计中的输出 token 数。
    cache_creation_input_tokens: int = 0  # int：用于创建缓存的输入 token 数。
    cache_read_input_tokens: int = 0  # int：命中缓存时读取的输入 token 数。
    reasoning_tokens: int = 0  # int：推理型 token 数，单独记录但不计入 total_tokens。

    @property
    def total_tokens(self) -> int:
        """计算总 token 数量。

        Args:
            None: 该属性不接收额外参数。
        Returns:
            int: 输入、输出与缓存相关 token 的总和，不含 reasoning token。
        """
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: 'TokenUsage') -> 'TokenUsage':
        """合并两份 token 使用统计。

        Args:
            other (TokenUsage): 需要累加到当前对象上的另一份统计结果。
        Returns:
            TokenUsage: 各字段逐项求和后的新统计对象。
        """
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_dict(self) -> JSONDict:
        """把 token 使用统计转换成 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 包含全部字段以及 `total_tokens` 汇总值的字典。
        """
        return {
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cache_creation_input_tokens': self.cache_creation_input_tokens,
            'cache_read_input_tokens': self.cache_read_input_tokens,
            'reasoning_tokens': self.reasoning_tokens,
            'total_tokens': self.total_tokens,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'TokenUsage':
        """从 JSON 字典恢复 token 使用统计对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典，支持兼容字段名。
        Returns:
            TokenUsage: 恢复后的 token 使用统计对象。
        """
        data = _as_dict(payload)
        return cls(
            input_tokens=_as_int(
                _first_present(data, 'input_tokens', 'prompt_tokens', 'inputTokens', default=0),
                0,
            ),
            output_tokens=_as_int(
                _first_present(
                    data,
                    'output_tokens',
                    'completion_tokens',
                    'outputTokens',
                    default=0,
                ),
                0,
            ),
            cache_creation_input_tokens=_as_int(
                _first_present(
                    data,
                    'cache_creation_input_tokens',
                    'cacheCreationInputTokens',
                    default=0,
                ),
                0,
            ),
            cache_read_input_tokens=_as_int(
                _first_present(
                    data,
                    'cache_read_input_tokens',
                    'cacheReadInputTokens',
                    default=0,
                ),
                0,
            ),
            reasoning_tokens=_as_int(
                _first_present(data, 'reasoning_tokens', 'reasoningTokens', default=0),
                0,
            ),
        )