"""预算限制相关契约。"""

from __future__ import annotations

from dataclasses import dataclass

from .coercion import _as_dict, _as_optional_float, _as_optional_int, _first_present
from .protocol import JSONDict


@dataclass(frozen=True)
class BudgetConfig:
    """运行期预算限制，用于保证安全和可预测性。"""

    max_total_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_reasoning_tokens: int | None = None
    max_total_cost_usd: float | None = None
    max_tool_calls: int | None = None
    max_delegated_tasks: int | None = None
    max_model_calls: int | None = None
    max_session_turns: int | None = None

    def to_dict(self) -> JSONDict:
        return {
            'max_total_tokens': self.max_total_tokens,
            'max_input_tokens': self.max_input_tokens,
            'max_output_tokens': self.max_output_tokens,
            'max_reasoning_tokens': self.max_reasoning_tokens,
            'max_total_cost_usd': self.max_total_cost_usd,
            'max_tool_calls': self.max_tool_calls,
            'max_delegated_tasks': self.max_delegated_tasks,
            'max_model_calls': self.max_model_calls,
            'max_session_turns': self.max_session_turns,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'BudgetConfig':
        data = _as_dict(payload)
        return cls(
            max_total_tokens=_as_optional_int(_first_present(data, 'max_total_tokens', 'maxTotalTokens')),
            max_input_tokens=_as_optional_int(_first_present(data, 'max_input_tokens', 'maxInputTokens')),
            max_output_tokens=_as_optional_int(_first_present(data, 'max_output_tokens', 'maxOutputTokens')),
            max_reasoning_tokens=_as_optional_int(_first_present(data, 'max_reasoning_tokens', 'maxReasoningTokens')),
            max_total_cost_usd=_as_optional_float(_first_present(data, 'max_total_cost_usd', 'maxTotalCostUsd')),
            max_tool_calls=_as_optional_int(_first_present(data, 'max_tool_calls', 'maxToolCalls')),
            max_delegated_tasks=_as_optional_int(_first_present(data, 'max_delegated_tasks', 'maxDelegatedTasks')),
            max_model_calls=_as_optional_int(_first_present(data, 'max_model_calls', 'maxModelCalls')),
            max_session_turns=_as_optional_int(_first_present(data, 'max_session_turns', 'maxSessionTurns')),
        )