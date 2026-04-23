"""使用统计与计费相关契约。"""

from __future__ import annotations

from dataclasses import dataclass

from ._coerce import _as_float, _as_int, _as_dict, _first_present
from .protocol import JSONDict


@dataclass(frozen=True)
class TokenUsage:
    """模型调用产生的 token 使用统计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: 'TokenUsage') -> 'TokenUsage':
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


@dataclass(frozen=True)
class ModelPricing:
    """用于估算会话成本的计费配置。"""

    input_cost_per_million_tokens_usd: float = 0.0
    output_cost_per_million_tokens_usd: float = 0.0
    cache_creation_input_cost_per_million_tokens_usd: float = 0.0
    cache_read_input_cost_per_million_tokens_usd: float = 0.0

    def estimate_cost_usd(self, usage: TokenUsage) -> float:
        input_cost = (usage.input_tokens / 1_000_000.0) * self.input_cost_per_million_tokens_usd
        output_cost = (usage.output_tokens / 1_000_000.0) * self.output_cost_per_million_tokens_usd
        cache_write_cost = (
            (usage.cache_creation_input_tokens / 1_000_000.0)
            * self.cache_creation_input_cost_per_million_tokens_usd
        )
        cache_read_cost = (
            (usage.cache_read_input_tokens / 1_000_000.0)
            * self.cache_read_input_cost_per_million_tokens_usd
        )
        return input_cost + output_cost + cache_write_cost + cache_read_cost

    def to_dict(self) -> JSONDict:
        return {
            'input_cost_per_million_tokens_usd': self.input_cost_per_million_tokens_usd,
            'output_cost_per_million_tokens_usd': self.output_cost_per_million_tokens_usd,
            'cache_creation_input_cost_per_million_tokens_usd': (
                self.cache_creation_input_cost_per_million_tokens_usd
            ),
            'cache_read_input_cost_per_million_tokens_usd': (
                self.cache_read_input_cost_per_million_tokens_usd
            ),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ModelPricing':
        data = _as_dict(payload)
        return cls(
            input_cost_per_million_tokens_usd=_as_float(
                _first_present(
                    data,
                    'input_cost_per_million_tokens_usd',
                    'inputCostPerMillionTokensUsd',
                    default=0.0,
                ),
                0.0,
            ),
            output_cost_per_million_tokens_usd=_as_float(
                _first_present(
                    data,
                    'output_cost_per_million_tokens_usd',
                    'outputCostPerMillionTokensUsd',
                    default=0.0,
                ),
                0.0,
            ),
            cache_creation_input_cost_per_million_tokens_usd=_as_float(
                _first_present(
                    data,
                    'cache_creation_input_cost_per_million_tokens_usd',
                    'cacheCreationInputCostPerMillionTokensUsd',
                    default=0.0,
                ),
                0.0,
            ),
            cache_read_input_cost_per_million_tokens_usd=_as_float(
                _first_present(
                    data,
                    'cache_read_input_cost_per_million_tokens_usd',
                    'cacheReadInputCostPerMillionTokensUsd',
                    default=0.0,
                ),
                0.0,
            ),
        )