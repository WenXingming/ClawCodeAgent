"""ISSUE-009 预算闸门：集中管理主循环的五维预算检查。"""

from __future__ import annotations

from dataclasses import dataclass

from budget.budget_snapshot import TokenBudgetSnapshot
from core_contracts.config import BudgetConfig
from core_contracts.usage import ModelPricing, TokenUsage


@dataclass
class BudgetGuard:
    """集中管理 `_execute_loop` 的预算闸门。"""

    budget: BudgetConfig
    pricing: ModelPricing
    cost_baseline: float

    def check_pre_model(
        self,
        *,
        turns_offset: int,
        turns_this_run: int,
        model_call_count: int,
        snapshot: TokenBudgetSnapshot,
        usage_delta: TokenUsage,
    ) -> str | None:
        return (
            self._check_session_turns(turns_offset, turns_this_run)
            or self._check_model_calls(model_call_count)
            or self._check_token(snapshot)
            or self._check_cost(usage_delta)
        )

    def check_post_tool(self, tool_call_count: int) -> str | None:
        return self._check_tool_calls(tool_call_count)

    def _check_session_turns(self, turns_offset: int, turns_this_run: int) -> str | None:
        if (
            self.budget.max_session_turns is not None
            and turns_offset + turns_this_run > self.budget.max_session_turns
        ):
            return 'session_turns_limit'
        return None

    def _check_model_calls(self, model_call_count: int) -> str | None:
        if (
            self.budget.max_model_calls is not None
            and model_call_count >= self.budget.max_model_calls
        ):
            return 'model_call_limit'
        return None

    def _check_token(self, snapshot: TokenBudgetSnapshot) -> str | None:
        if snapshot.is_hard_over:
            return 'token_limit'
        return None

    def _check_cost(self, usage_delta: TokenUsage) -> str | None:
        if self.budget.max_total_cost_usd is not None:
            current_cost = self.cost_baseline + self.pricing.estimate_cost_usd(usage_delta)
            if current_cost >= self.budget.max_total_cost_usd:
                return 'cost_limit'
        return None

    def _check_tool_calls(self, tool_call_count: int) -> str | None:
        if (
            self.budget.max_tool_calls is not None
            and tool_call_count >= self.budget.max_tool_calls
        ):
            return 'tool_call_limit'
        return None