"""模型计费相关契约。"""

from __future__ import annotations

from dataclasses import dataclass

from ._coerce import _as_dict, _as_float, _first_present
from .protocol import JSONDict
from .token_usage import TokenUsage


@dataclass(frozen=True)
class ModelPricing:
    """用于估算会话成本的计费配置。

    存储模型的每百万 token 计费（美元），用于根据 TokenUsage 计算费用。
    """

    input_cost_per_million_tokens_usd: float = 0.0
    output_cost_per_million_tokens_usd: float = 0.0
    cache_creation_input_cost_per_million_tokens_usd: float = 0.0
    cache_read_input_cost_per_million_tokens_usd: float = 0.0

    def estimate_cost_usd(self, usage: TokenUsage) -> float:
        """根据 TokenUsage 估算本次调用的成本。"""
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