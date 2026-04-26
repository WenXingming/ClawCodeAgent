"""ISSUE-009 预算闸门：集中管理主循环的五维预算检查。"""

from __future__ import annotations

from dataclasses import dataclass

from budget.budget_snapshot import TokenBudgetSnapshot
from core_contracts.config import BudgetConfig
from core_contracts.usage import ModelPricing, TokenUsage


@dataclass
class BudgetGuard:
    """集中管理 `_execute_loop` 的预算闸门。
    
    在模型调用前/工具调用后进行五维预算检查（turns/model_calls/tokens/cost/tool_calls），
    若触发任何限制则返回限制类型标识符，否则返回None。
    """

    budget: BudgetConfig  # 预算配置对象（包含所有阈值设置）
    pricing: ModelPricing  # 模型计费配置（用于成本估算）
    cost_baseline: float  # 当前会话已累计成本（美元）

    def check_pre_model(
        self,
        *,
        turns_offset: int,
        turns_this_run: int,
        model_call_count: int,
        snapshot: TokenBudgetSnapshot,
        usage_delta: TokenUsage,
    ) -> str | None:
        """模型调用前的五维预算预检。
        
        依次检查：turns限制 → model_calls限制 → tokens限制 → 成本限制
        若任一检查失败，立即返回限制标识符；全部通过则返回None。
        
        Args:
            turns_offset (int): 已完成的turn数
            turns_this_run (int): 本次新增的turn数
            model_call_count (int): 已发生的模型调用次数
            snapshot (TokenBudgetSnapshot): 本次调用的token预算快照
            usage_delta (TokenUsage): 预估的本次调用token消耗
            
        Returns:
            str | None: 触发的限制标识符或None（'session_turns_limit'、'model_call_limit'、
                       'token_limit'、'cost_limit'）
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
            tool_call_count (int): 已发生的工具调用次数
            
        Returns:
            str | None: 触发的限制标识符或None（'tool_call_limit'）
        """
        return self._check_tool_calls(tool_call_count)

    def _check_session_turns(self, turns_offset: int, turns_this_run: int) -> str | None:
        """内部方法：检查会话turn数是否超限。
        
        Args:
            turns_offset (int): 已完成的turn数
            turns_this_run (int): 本次新增的turn数
            
        Returns:
            str | None: 'session_turns_limit' 若超限，否则None
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
            model_call_count (int): 已发生的模型调用次数
            
        Returns:
            str | None: 'model_call_limit' 若超限，否则None
        """
        if (
            self.budget.max_model_calls is not None
            and model_call_count >= self.budget.max_model_calls
        ):
            return 'model_call_limit'
        return None

    def _check_token(self, snapshot: TokenBudgetSnapshot) -> str | None:
        """内部方法：检查是否超过token硬限制。
        
        Args:
            snapshot (TokenBudgetSnapshot): Token预算快照
            
        Returns:
            str | None: 'token_limit' 若超过硬限制，否则None
        """
        if snapshot.is_hard_over:
            return 'token_limit'
        return None

    def _check_cost(self, usage_delta: TokenUsage) -> str | None:
        """内部方法：检查累计成本是否超限。
        
        根据当前成本基线、本次调用消耗token、计费配置估算新成本。
        
        Args:
            usage_delta (TokenUsage): 本次调用预估的token消耗
            
        Returns:
            str | None: 'cost_limit' 若超过成本限制，否则None
        """
        if self.budget.max_total_cost_usd is not None:
            current_cost = self.cost_baseline + self.pricing.estimate_cost_usd(usage_delta)
            if current_cost >= self.budget.max_total_cost_usd:
                return 'cost_limit'
        return None

    def _check_tool_calls(self, tool_call_count: int) -> str | None:
        """内部方法：检查工具调用次数是否超限。
        
        Args:
            tool_call_count (int): 已发生的工具调用次数
            
        Returns:
            str | None: 'tool_call_limit' 若超限，否则None
        """
        if (
            self.budget.max_tool_calls is not None
            and tool_call_count >= self.budget.max_tool_calls
        ):
            return 'tool_call_limit'
        return None