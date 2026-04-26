"""使用统计与计费相关契约。"""

from __future__ import annotations

from dataclasses import dataclass

from ._coerce import _as_float, _as_int, _as_dict, _first_present
from .protocol import JSONDict


@dataclass(frozen=True)
class TokenUsage:
    """模型调用产生的 token 使用统计。
    
    记录一次或多次模型调用的 token 消耗详情，包括输入/输出/缓存等维度。
    """

    input_tokens: int = 0  # 输入 token 数量
    output_tokens: int = 0  # 输出 token 数量
    cache_creation_input_tokens: int = 0  # 缓存写入的输入 token 数量
    cache_read_input_tokens: int = 0  # 缓存命中的输入 token 数量
    reasoning_tokens: int = 0  # 推理过程消耗的 token 数量

    @property
    def total_tokens(self) -> int:
        """计算总 token 数量（不含推理 token）。
        
        Returns:
            int: 输入+输出+缓存相关 token 总数
        """
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
    """用于估算会话成本的计费配置。
    
    存储模型的每百万token计费（美元），用于根据TokenUsage计算费用。
    """

    input_cost_per_million_tokens_usd: float = 0.0  # 输入 token 的每百万单位成本（美元）
    output_cost_per_million_tokens_usd: float = 0.0  # 输出 token 的每百万单位成本（美元）
    cache_creation_input_cost_per_million_tokens_usd: float = 0.0  # 缓存写入 token 的成本（美元）
    cache_read_input_cost_per_million_tokens_usd: float = 0.0  # 缓存命中 token 的成本（美元）

    def estimate_cost_usd(self, usage: TokenUsage) -> float:
        """根据 TokenUsage 估算本次调用的成本。
        
        按照各维度 token 数量 × 单价计算成本；返回美元单位的浮点数。
        
        Args:
            usage (TokenUsage): 本次调用的 token 使用统计
            
        Returns:
            float: 估算的成本（美元），精度为小数点后若干位
        """
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
        """序列化为字典。
        
        Returns:
            JSONDict: 包含所有计费配置的字典
        """
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
        """反序列化为 ModelPricing 对象。
        
        Args:
            payload (JSONDict | None): 待反序列化的计费配置字典
            
        Returns:
            ModelPricing: 反序列化后的计费对象
        """
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