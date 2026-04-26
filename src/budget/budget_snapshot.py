"""ISSUE-009 Token Budget 快照对象。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenBudgetSnapshot:
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