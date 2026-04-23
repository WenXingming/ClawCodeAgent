"""ISSUE-009 预算闸门：集中管理 _execute_loop 中的全维度预算检查逻辑。

公共 API
--------
    BudgetGuard  — 持有预算配置与基线成本，提供两个检查方法：
        check_pre_model  — 模型调用前的四维预算检查。
        check_post_tool  — 每次工具执行后的工具调用次数检查。

设计说明
--------
将五个预算闸门从 _execute_loop 内联代码中解耦，使主循环只需在两处
调用检查方法，便于独立测试和后续维度扩展（如 ISSUE-010/011 的
context pressure 指标）。

每个维度各自对应一个私有子方法（_check_*），公共方法按序组合调用：

    check_pre_model  → _check_session_turns
                     → _check_model_calls
                     → _check_token
                     → _check_cost

    check_post_tool  → _check_tool_calls
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contract_types import BudgetConfig, ModelPricing, TokenUsage
from .token_budget import TokenBudgetSnapshot


@dataclass
class BudgetGuard:
    """集中管理 _execute_loop 全维度预算闸门。

    Attributes:
        budget:        来自 AgentRuntimeConfig.budget_config 的预算配置。
        pricing:       来自 OpenAIClient.config.pricing 的计费配置，用于 cost 估算。
        cost_baseline: 历史成本基线（run=0.0，resume=上次 total_cost_usd）。
    """

    budget: BudgetConfig
    pricing: ModelPricing
    cost_baseline: float

    # ------------------------------------------------------------------
    # 公共检查方法
    # ------------------------------------------------------------------

    def check_pre_model(
        self,
        *,
        turns_offset: int,
        turns_this_run: int,
        model_call_count: int,
        snapshot: TokenBudgetSnapshot,
        usage_delta: TokenUsage,
    ) -> str | None:
        """模型调用前的四维预算检查，按优先级依次调用子方法。

        Returns:
            首个触发的 stop_reason 字符串；全部通过时返回 None。
        """
        return (
            self._check_session_turns(turns_offset, turns_this_run)
            or self._check_model_calls(model_call_count)
            or self._check_token(snapshot)
            or self._check_cost(usage_delta)
        )

    def check_post_tool(self, tool_call_count: int) -> str | None:
        """每次工具执行后的工具调用次数检查。

        Returns:
            'tool_call_limit' 或 None。
        """
        return self._check_tool_calls(tool_call_count)

    # ------------------------------------------------------------------
    # 私有子检查方法（每维度独立，便于测试与扩展）
    # ------------------------------------------------------------------

    def _check_session_turns(self, turns_offset: int, turns_this_run: int) -> str | None:
        """1. 会话累计轮数上限（含 resume 历史）。"""
        if (
            self.budget.max_session_turns is not None
            and turns_offset + turns_this_run > self.budget.max_session_turns
        ):
            return 'session_turns_limit'
        return None

    def _check_model_calls(self, model_call_count: int) -> str | None:
        """2. 模型调用次数上限。"""
        if (
            self.budget.max_model_calls is not None
            and model_call_count >= self.budget.max_model_calls
        ):
            return 'model_call_limit'
        return None

    def _check_token(self, snapshot: TokenBudgetSnapshot) -> str | None:
        """3. Token 硬超限（基于 TokenBudgetSnapshot.is_hard_over）。"""
        if snapshot.is_hard_over:
            return 'token_limit'
        return None

    def _check_cost(self, usage_delta: TokenUsage) -> str | None:
        """4. 会话总成本上限。"""
        if self.budget.max_total_cost_usd is not None:
            current_cost = self.cost_baseline + self.pricing.estimate_cost_usd(usage_delta)
            if current_cost >= self.budget.max_total_cost_usd:
                return 'cost_limit'
        return None

    def _check_tool_calls(self, tool_call_count: int) -> str | None:
        """5. 工具调用次数上限（每次工具执行后）。"""
        if (
            self.budget.max_tool_calls is not None
            and tool_call_count >= self.budget.max_tool_calls
        ):
            return 'tool_call_limit'
        return None
