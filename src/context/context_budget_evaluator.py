"""ISSUE-009 Token Budget 预检与投影估算。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from context.context_token_estimator import ContextTokenEstimator


@dataclass(frozen=True)
class ContextBudgetSnapshot:
    """描述一次 token 预算预检的结果快照。
    
    由ContextBudgetEvaluator在判断当前模型调用是否会超预算时生成，
    包含预估的token数、限制值、以及是否超过限制的标志。
    """

    projected_input_tokens: int  # 预估的本次输入 token 数量
    output_reserve_tokens: int  # 为模型输出预留的 token 数量
    hard_input_limit: int | None  # 硬限制（若超过则报错中止）；None表示无硬限制
    soft_input_limit: int | None  # 软限制（若超过则警告但继续）；None表示无软限制
    is_hard_over: bool  # 是否超过硬限制
    is_soft_over: bool  # 是否超过软限制


OUTPUT_RESERVE_TOKENS: int = 4_096
SOFT_BUFFER_TOKENS: int = 13_000


@dataclass(frozen=True)
class ContextBudgetEvaluator:
    """基于 token 估算结果生成预算快照。
    
    在模型调用前进行预检，估算输入token数并与预算限制比对，生成是否超预算的决策结果快照。
    """

    token_estimator: ContextTokenEstimator = field(default_factory=ContextTokenEstimator)  # Token估算器实例
    output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS  # 默认输出保留token数（4096）
    soft_buffer_tokens: int = SOFT_BUFFER_TOKENS  # 默认软限制缓冲token数（13000）

    def evaluate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_input_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        soft_buffer_tokens: int | None = None,
    ) -> ContextBudgetSnapshot:
        """预检本次调用是否超预算。
        
        流程：
        1. 估算消息+工具定义的输入token总数（projected）
        2. 若无max_input_tokens限制，返回projected但不标记超限
        3. 若有限制，计算硬限制可用空间(usable)和软限制空间(soft_limit)
        4. 对比projected与两个限制，标记是否超限
        
        Args:
            messages (list[dict[str, Any]]): 消息列表
            tools (list[dict[str, Any]] | None): 工具定义列表；None时作为空列表处理
            max_input_tokens (int | None): 硬限制（若超过返回error）；None表示无限制
            output_reserve_tokens (int | None): 本次输出保留空间；None使用默认值
            soft_buffer_tokens (int | None): 本次软限制缓冲；None使用默认值
            
        Returns:
            ContextBudgetSnapshot: 预算预检快照，包含projected/limit/超限标志
        """
        output_reserve = self.output_reserve_tokens if output_reserve_tokens is None else output_reserve_tokens
        soft_buffer = self.soft_buffer_tokens if soft_buffer_tokens is None else soft_buffer_tokens
        projected = self.token_estimator.estimate_messages(messages) + self.token_estimator.estimate_tools(tools or [])

        if max_input_tokens is None:
            return ContextBudgetSnapshot(
                projected_input_tokens=projected,
                output_reserve_tokens=output_reserve,
                hard_input_limit=None,
                soft_input_limit=None,
                is_hard_over=False,
                is_soft_over=False,
            )

        hard_limit = max_input_tokens
        usable = hard_limit - output_reserve
        soft_limit = max(0, usable - soft_buffer)

        return ContextBudgetSnapshot(
            projected_input_tokens=projected,
            output_reserve_tokens=output_reserve,
            hard_input_limit=hard_limit,
            soft_input_limit=soft_limit,
            is_hard_over=projected > usable,
            is_soft_over=projected > soft_limit,
        )
