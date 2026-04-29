"""集中管理主循环预算闸门的预算检查逻辑。

本模块负责把会话运行过程中的多维预算判断收敛到单一入口，供主循环在模型调用前和工具调用后统一执行预算检查。其职责仅限于判定是否命中预算限制，并返回上层可识别的限制标识，不直接执行中断或恢复动作。
"""

from __future__ import annotations

from dataclasses import dataclass

from context.budget_projection import BudgetProjection
from core_contracts.budget import BudgetConfig
from core_contracts.model_pricing import ModelPricing
from core_contracts.token_usage import TokenUsage


@dataclass
class BudgetGuard:
    """集中管理 `_execute_loop` 的预算闸门。

    典型工作流如下：
    1. 主循环在发起模型调用前调用 `check_pre_model()`。
    2. 若模型阶段通过，再在工具执行后调用 `check_post_tool()`。
    3. 上层根据返回的限制标识决定是否停止会话或输出说明。
    """

    budget: BudgetConfig  # BudgetConfig：预算配置对象，包含各维度阈值。
    pricing: ModelPricing  # ModelPricing：模型计费配置，用于估算本轮成本增量。
    cost_baseline: float  # float：当前会话在本轮检查前已经累计的成本，单位为美元。

    def check_pre_model(
        self,
        *,
        turns_offset: int,
        turns_this_run: int,
        model_call_count: int,
        snapshot: BudgetProjection,
        usage_delta: TokenUsage,
    ) -> str | None:
        """模型调用前的五维预算预检。

        依次检查：turns 限制 -> model_calls 限制 -> token 硬限制 -> 成本限制。
        任一检查失败都会立即返回对应的限制标识；全部通过时返回 None。

        Args:
            turns_offset (int): 当前会话在本轮之前已经完成的 turn 数。
            turns_this_run (int): 当前 run/resume 调用中新增加的 turn 数。
            model_call_count (int): 到当前为止已经发生的模型调用次数。
            snapshot (BudgetProjection): 本次模型调用前生成的上下文预算快照。
            usage_delta (TokenUsage): 预估的本次模型调用 token 消耗。
        Returns:
            str | None: 命中的限制标识；未命中任何限制时返回 None。
        """
        return (
            self._check_session_turns(turns_offset, turns_this_run)
            or self._check_model_calls(model_call_count)
            or self._check_token(snapshot)
            or self._check_cost(usage_delta)
        )

    def check_post_tool(self, tool_call_count: int) -> str | None:
        """工具调用后的预算检查（仅检查tool_calls限制）。

        Args:
            tool_call_count (int): 到当前为止已经发生的工具调用次数。
        Returns:
            str | None: 命中的限制标识；未超限时返回 None。
        """
        return self._check_tool_calls(tool_call_count)

    def _check_session_turns(self, turns_offset: int, turns_this_run: int) -> str | None:
        """内部方法：检查会话turn数是否超限。

        Args:
            turns_offset (int): 当前会话在本轮之前已经完成的 turn 数。
            turns_this_run (int): 当前 run/resume 调用中新增加的 turn 数。
        Returns:
            str | None: 若超过会话 turn 上限则返回对应限制标识，否则返回 None。
        """
        if (
            self.budget.max_session_turns is not None
            and turns_offset + turns_this_run > self.budget.max_session_turns
        ):
            return 'session_turns_limit'
        return None

    def _check_model_calls(self, model_call_count: int) -> str | None:
        """内部方法：检查模型调用次数是否超限。

        Args:
            model_call_count (int): 到当前为止已经发生的模型调用次数。
        Returns:
            str | None: 若超过模型调用次数上限则返回对应限制标识，否则返回 None。
        """
        if (
            self.budget.max_model_calls is not None
            and model_call_count >= self.budget.max_model_calls
        ):
            return 'model_call_limit'
        return None

    def _check_token(self, snapshot: BudgetProjection) -> str | None:
        """内部方法：检查是否超过token硬限制。

        Args:
            snapshot (BudgetProjection): 本次模型调用对应的预算快照。
        Returns:
            str | None: 若超过 token 硬限制则返回对应限制标识，否则返回 None。
        """
        if snapshot.is_hard_over:
            return 'token_limit'
        return None

    def _check_cost(self, usage_delta: TokenUsage) -> str | None:
        """内部方法：检查累计成本是否超限。

        根据当前成本基线、本次调用消耗token、计费配置估算新成本。

        Args:
            usage_delta (TokenUsage): 本次模型调用预估的 token 消耗。
        Returns:
            str | None: 若超过成本上限则返回对应限制标识，否则返回 None。
        """
        if self.budget.max_total_cost_usd is not None:
            current_cost = self.cost_baseline + self.pricing.estimate_cost_usd(usage_delta)
            if current_cost >= self.budget.max_total_cost_usd:
                return 'cost_limit'
        return None

    def _check_tool_calls(self, tool_call_count: int) -> str | None:
        """内部方法：检查工具调用次数是否超限。

        Args:
            tool_call_count (int): 到当前为止已经发生的工具调用次数。
        Returns:
            str | None: 若超过工具调用次数上限则返回对应限制标识，否则返回 None。
        """
        if (
            self.budget.max_tool_calls is not None
            and tool_call_count >= self.budget.max_tool_calls
        ):
            return 'tool_call_limit'
        return None