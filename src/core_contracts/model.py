"""模型配置、计费与调用协议契约。

合并 model.py、model_pricing.py 与 openai_contracts.py 的模型相关类型，
提供从配置、计费到调用接口的完整模型契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable

from ._coercion import _as_bool, _as_dict, _as_float, _as_str, _first_present
from .primitives import JSONDict, TokenUsage


@dataclass(frozen=True)
class StructuredOutputSpec:
    """描述模型应产出的结构化输出 schema。"""

    name: str = ''  # str：结构化输出名称。
    schema: JSONDict = None  # JSONDict：JSON Schema 定义。
    strict: bool = False  # bool：是否启用严格模式。

    def __post_init__(self) -> None:
        if self.schema is None:
            object.__setattr__(self, 'schema', {})

    def to_dict(self) -> JSONDict:
        """把结构化输出规格转换为字典。
        Returns:
            JSONDict: 包含 name/schema/strict 的字典。
        """
        return {
            'name': self.name,
            'schema': dict(self.schema),
            'strict': self.strict,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'StructuredOutputSpec | None':
        """从 JSON 字典恢复结构化输出规格。
        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            StructuredOutputSpec | None: 恢复后的对象；数据不完整时返回 None。
        """
        data = _as_dict(payload)
        name = _as_str(data.get('name'), '').strip()
        schema = data.get('schema')
        if not name or not isinstance(schema, dict):
            return None
        return cls(
            name=name,
            schema=_as_dict(schema),
            strict=_as_bool(_first_present(data, 'strict', default=False), False),
        )


@dataclass(frozen=True)
class ModelConfig:
    """描述模型后端的完整连接与运行配置。"""

    model: str  # str：模型 ID。
    base_url: str = 'http://127.0.0.1:8000/v1'  # str：模型 API base URL。
    api_key: str = ''  # str：模型 API 认证密钥。
    temperature: float = 0.0  # float：推理温度参数。
    timeout_seconds: float = 120.0  # float：单次 HTTP 请求超时时间，单位秒。
    pricing: 'ModelPricing' = None  # ModelPricing：模型计费配置。

    def __post_init__(self) -> None:
        if self.pricing is None:
            object.__setattr__(self, 'pricing', ModelPricing())

    def to_dict(self) -> JSONDict:
        """把模型配置转换为字典。
        Returns:
            JSONDict: 包含全部字段的可序列化字典。
        """
        return {
            'model': self.model,
            'base_url': self.base_url,
            'api_key': self.api_key,
            'temperature': self.temperature,
            'timeout_seconds': self.timeout_seconds,
            'pricing': self.pricing.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ModelConfig':
        """从 JSON 字典恢复模型配置。
        Args:
            payload (JSONDict | None): 待反序列化的原始字典，兼容 camelCase 字段名。
        Returns:
            ModelConfig: 恢复后的配置对象。
        """
        data = _as_dict(payload)
        model = _as_str(data.get('model'), '').strip() or 'unknown-model'
        return cls(
            model=model,
            base_url=_as_str(_first_present(data, 'base_url', 'baseUrl', default=''), ''),
            api_key=_as_str(_first_present(data, 'api_key', 'apiKey', default=''), ''),
            temperature=_as_float(_first_present(data, 'temperature', default=0.0), 0.0),
            timeout_seconds=_as_float(_first_present(data, 'timeout_seconds', 'timeoutSeconds', default=120.0), 120.0),
            pricing=ModelPricing.from_dict(_first_present(data, 'pricing', default=None)),
        )


@dataclass(frozen=True)
class ModelPricing:
    """用于估算会话成本的计费配置。"""

    input_cost_per_million_tokens_usd: float = 0.0  # float：普通输入 token 的每百万单价，单位美元。
    output_cost_per_million_tokens_usd: float = 0.0  # float：普通输出 token 的每百万单价，单位美元。
    cache_creation_input_cost_per_million_tokens_usd: float = 0.0  # float：缓存写入的每百万单价。
    cache_read_input_cost_per_million_tokens_usd: float = 0.0  # float：缓存命中的每百万单价。

    def estimate_cost_usd(self, usage: TokenUsage) -> float:
        """根据 token 使用统计估算本次调用成本。
        Args:
            usage (TokenUsage): 需要估算成本的 token 使用统计。
        Returns:
            float: 按当前计费配置换算得到的美元成本。
        """
        return (
            (usage.input_tokens / 1_000_000.0) * self.input_cost_per_million_tokens_usd
            + (usage.output_tokens / 1_000_000.0) * self.output_cost_per_million_tokens_usd
            + (usage.cache_creation_input_tokens / 1_000_000.0)
            * self.cache_creation_input_cost_per_million_tokens_usd
            + (usage.cache_read_input_tokens / 1_000_000.0)
            * self.cache_read_input_cost_per_million_tokens_usd
        )

    def to_dict(self) -> JSONDict:
        """把计费配置转换为 JSON 字典。
        Returns:
            JSONDict: 包含全部单价字段的字典。
        """
        return {
            'input_cost_per_million_tokens_usd': self.input_cost_per_million_tokens_usd,
            'output_cost_per_million_tokens_usd': self.output_cost_per_million_tokens_usd,
            'cache_creation_input_cost_per_million_tokens_usd': self.cache_creation_input_cost_per_million_tokens_usd,
            'cache_read_input_cost_per_million_tokens_usd': self.cache_read_input_cost_per_million_tokens_usd,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ModelPricing':
        """从 JSON 字典恢复计费配置。
        Args:
            payload (JSONDict | None): 待反序列化的原始字典，兼容 camelCase 字段名。
        Returns:
            ModelPricing: 恢复后的计费配置对象。
        """
        data = _as_dict(payload)
        return cls(
            input_cost_per_million_tokens_usd=_as_float(
                _first_present(data, 'input_cost_per_million_tokens_usd', 'inputCostPerMillionTokensUsd', default=0.0), 0.0
            ),
            output_cost_per_million_tokens_usd=_as_float(
                _first_present(data, 'output_cost_per_million_tokens_usd', 'outputCostPerMillionTokensUsd', default=0.0), 0.0
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


@runtime_checkable
class ModelClient(Protocol):
    """跨模块使用的模型调用最小接口协议。"""

    model_config: ModelConfig  # ModelConfig：当前使用的模型配置。

    def complete(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> 'OneTurnResponse':
        """执行一次非流式模型调用。"""

    def stream(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> Iterator['StreamEvent']:
        """执行一次流式模型调用。"""

    def complete_stream(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> 'OneTurnResponse':
        """执行一次流式聚合模型调用。"""
