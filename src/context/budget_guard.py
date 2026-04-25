"""ISSUE-009 预算闸门：集中管理主循环的五维预算检查。

本模块把 runtime 主循环中的预算判断拆成可单测的 `BudgetGuard`，并维持两条公开调用主线：
1. `check_pre_model()`：在模型调用前按优先级检查 session_turns、model_calls、token、cost。
2. `check_post_tool()`：在每次工具执行后检查 tool_calls。

类内方法顺序按“公开入口优先，再顺着首次调用链往下读”组织，便于从主流程直接定位对应的私有维度检查。
"""

from __future__ import annotations

from dataclasses import dataclass

from core_contracts.config import BudgetConfig
from core_contracts.usage import ModelPricing, TokenUsage
from .token_budget import TokenBudgetSnapshot


@dataclass
class BudgetGuard:
    """集中管理 `_execute_loop` 的预算闸门。

    典型工作流如下：
    1. 主循环在每轮模型调用前调用 `check_pre_model()`。
    2. 若本轮产生工具调用，主循环在每个工具执行后调用 `check_post_tool()`。
    3. 任一维度命中上限时返回 stop_reason，由上层统一构造提前退出结果。
    """

    budget: BudgetConfig  # BudgetConfig：当前会话的预算上限配置。
    pricing: ModelPricing  # ModelPricing：用于把 usage_delta 转换为美元成本的计费规则。
    cost_baseline: float  # float：resume 场景下的历史累计成本基线。

    def check_pre_model(
        self,
        *,
        turns_offset: int,
        turns_this_run: int,
        model_call_count: int,
        snapshot: TokenBudgetSnapshot,
        usage_delta: TokenUsage,
    ) -> str | None:
        """执行模型调用前的四维预算检查。

        Args:
            turns_offset (int): resume 场景下已完成的历史轮数。
            turns_this_run (int): 当前 run/resume 调用内已经推进到的轮次。
            model_call_count (int): 当前执行中已经发生的模型调用次数。
            snapshot (TokenBudgetSnapshot): 本轮 token 预算预检快照。
            usage_delta (TokenUsage): 当前执行累计产生的增量 token 使用量。

        Returns:
            str | None: 首个触发的 stop_reason；若全部通过则返回 None。
        """
        return (
            self._check_session_turns(turns_offset, turns_this_run)
            or self._check_model_calls(model_call_count)
            or self._check_token(snapshot)
            or self._check_cost(usage_delta)
        )

    def check_post_tool(self, tool_call_count: int) -> str | None:
        """执行工具调用后的预算检查。

        Args:
            tool_call_count (int): 当前会话累计执行的工具调用次数。

        Returns:
            str | None: 触发工具调用上限时返回 `tool_call_limit`，否则返回 None。
        """
        return self._check_tool_calls(tool_call_count)

    def _check_session_turns(self, turns_offset: int, turns_this_run: int) -> str | None:
        """检查会话累计轮数上限。

        Args:
            turns_offset (int): resume 前已完成的历史轮数。
            turns_this_run (int): 当前执行已消耗的轮数。

        Returns:
            str | None: 超限时返回 `session_turns_limit`，否则返回 None。
        """
        if (
            self.budget.max_session_turns is not None
            and turns_offset + turns_this_run > self.budget.max_session_turns
        ):
            return 'session_turns_limit'
        return None

    def _check_model_calls(self, model_call_count: int) -> str | None:
        """检查模型调用次数上限。

        Args:
            model_call_count (int): 当前执行已发生的模型调用次数。

        Returns:
            str | None: 超限时返回 `model_call_limit`，否则返回 None。
        """
        if (
            self.budget.max_model_calls is not None
            and model_call_count >= self.budget.max_model_calls
        ):
            return 'model_call_limit'
        return None

    def _check_token(self, snapshot: TokenBudgetSnapshot) -> str | None:
        """检查 token 是否已达到硬上限。

        Args:
            snapshot (TokenBudgetSnapshot): 当前轮次的 token 预算快照。

        Returns:
            str | None: 硬超限时返回 `token_limit`，否则返回 None。
        """
        if snapshot.is_hard_over:
            return 'token_limit'
        return None

    def _check_cost(self, usage_delta: TokenUsage) -> str | None:
        """检查会话总成本上限。

        Args:
            usage_delta (TokenUsage): 当前执行累计产生的增量 token 使用量。

        Returns:
            str | None: 超限时返回 `cost_limit`，否则返回 None。
        """
        if self.budget.max_total_cost_usd is not None:
            current_cost = self.cost_baseline + self.pricing.estimate_cost_usd(usage_delta)
            if current_cost >= self.budget.max_total_cost_usd:
                return 'cost_limit'
        return None

    def _check_tool_calls(self, tool_call_count: int) -> str | None:
        """检查工具调用次数上限。

        Args:
            tool_call_count (int): 当前会话累计执行的工具调用次数。

        Returns:
            str | None: 超限时返回 `tool_call_limit`，否则返回 None。
        """
        if (
            self.budget.max_tool_calls is not None
            and tool_call_count >= self.budget.max_tool_calls
        ):
            return 'tool_call_limit'
        return None
