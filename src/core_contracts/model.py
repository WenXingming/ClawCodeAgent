"""模型配置与结构化输出契约。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .coercion import _as_bool, _as_dict, _as_float, _as_str, _first_present
from .model_pricing import ModelPricing
from .protocol import JSONDict


@dataclass(frozen=True)
class StructuredOutputSpec:
    """可选的结构化输出 schema 配置。"""

    name: str
    schema: JSONDict
    strict: bool = False

    def to_dict(self) -> JSONDict:
        return {
            'name': self.name,
            'schema': dict(self.schema),
            'strict': self.strict,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'StructuredOutputSpec | None':
        data = _as_dict(payload)
        name = _as_str(data.get('name'), '').strip()
        schema = data.get('schema')
        if not name or not isinstance(schema, dict):
            return None
        return cls(
            name=name,
            schema=dict(schema),
            strict=_as_bool(data.get('strict'), False),
        )


@dataclass(frozen=True)
class ModelConfig:
    """OpenAI-compatible 客户端使用的模型后端配置。"""

    model: str
    base_url: str = 'http://127.0.0.1:8000/v1'
    api_key: str = 'local-token'
    temperature: float = 0.0
    timeout_seconds: float = 120.0
    pricing: ModelPricing = field(default_factory=ModelPricing)

    def to_dict(self) -> JSONDict:
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
        data = _as_dict(payload)
        model = _as_str(data.get('model'), '').strip() or 'unknown-model'
        return cls(
            model=model,
            base_url=_as_str(
                _first_present(data, 'base_url', 'baseUrl', default='http://127.0.0.1:8000/v1'),
                'http://127.0.0.1:8000/v1',
            ),
            api_key=_as_str(_first_present(data, 'api_key', 'apiKey', default='local-token'), 'local-token'),
            temperature=_as_float(data.get('temperature'), 0.0),
            timeout_seconds=_as_float(
                _first_present(data, 'timeout_seconds', 'timeoutSeconds', default=120.0),
                120.0,
            ),
            pricing=ModelPricing.from_dict(data.get('pricing')),
        )