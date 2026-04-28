"""定义模型计费配置与成本估算契约。

本模块负责描述模型在不同 token 维度上的单价配置，并提供基于 `TokenUsage` 的统一成本估算入口。预算检查与运行结果统计都会依赖该契约保持成本口径一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from ._coerce import _as_dict, _as_float, _first_present
from .protocol import JSONDict
from .token_usage import TokenUsage


@dataclass(frozen=True)
class ModelPricing:
    """用于估算会话成本的计费配置。

    该对象存储模型在每百万 token 维度上的计费价格，并负责把 `TokenUsage` 转换为美元成本。外部通常在预算检查和最终运行结果汇总时调用 `estimate_cost_usd()`。
    """

    input_cost_per_million_tokens_usd: float = 0.0  # float：普通输入 token 的每百万单价，单位为美元。
    output_cost_per_million_tokens_usd: float = 0.0  # float：普通输出 token 的每百万单价，单位为美元。
    cache_creation_input_cost_per_million_tokens_usd: float = 0.0  # float：缓存写入输入 token 的每百万单价，单位为美元。
    cache_read_input_cost_per_million_tokens_usd: float = 0.0  # float：缓存读取输入 token 的每百万单价，单位为美元。

    def estimate_cost_usd(self, usage: TokenUsage) -> float:
        """根据 token 使用统计估算本次调用成本。

        Args:
            usage (TokenUsage): 需要估算成本的 token 使用统计。
        Returns:
            float: 按当前计费配置换算得到的美元成本。
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
        """把计费配置转换成 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 包含全部单价字段的字典。
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
        """从 JSON 字典恢复计费配置对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典，支持兼容字段名。
        Returns:
            ModelPricing: 恢复后的计费配置对象。
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