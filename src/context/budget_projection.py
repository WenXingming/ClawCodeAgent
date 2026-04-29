"""执行模型调用前的上下文预算投影。

本模块提供 BudgetProjector，根据消息列表与工具 schema 的启发式 token 估算，
生成本次调用的预算快照（BudgetProjection），供后续 snip / compact / guard 链路使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .token_estimator import TokenEstimator
from core_contracts.context import BudgetProjection


OUTPUT_RESERVE_TOKENS: int = 4_096   # 默认从输入上限中预留给模型输出的 token 数。
SOFT_BUFFER_TOKENS: int = 13_000     # 默认软缓冲区大小：在硬限之前提前触发 snip/compact。


@dataclass(frozen=True)
class BudgetProjector:
    """基于 token 估算结果生成上下文预算投影。

    核心工作流：
    1. 调用 ContextTokenEstimator 分别估算消息与工具的 token 开销；
    2. 对比 max_input_tokens 硬限与软缓冲，生成 BudgetProjection 快照；
    3. 快照由调用方决策是否触发 snip / compact 或直接中止。
    """

    token_estimator: TokenEstimator = field(default_factory=TokenEstimator)
    # ContextTokenEstimator：共享的启发式 token 估算器实例。

    output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS
    # int：从输入上限中预留给模型输出的默认 token 数；可在调用时覆盖。

    soft_buffer_tokens: int = SOFT_BUFFER_TOKENS
    # int：软缓冲区大小；投影超出（硬限 - 输出预留 - 软缓冲）时触发 soft_over。

    def project(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_input_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        soft_buffer_tokens: int | None = None,
    ) -> BudgetProjection:
        """预检本次模型调用的 token 预算并返回快照。

        Args:
            messages (list[dict[str, Any]]): 当前会话消息列表。
            tools (list[dict[str, Any]] | None): 当前可见工具 schema 列表；None 等同于空列表。
            max_input_tokens (int | None): 输入 token 硬上限；None 表示不设限制。
            output_reserve_tokens (int | None): 输出预留 token 覆盖值；None 使用实例默认值。
            soft_buffer_tokens (int | None): 软缓冲 token 覆盖值；None 使用实例默认值。
        Returns:
            BudgetProjection: 本次调用的预算快照，含 projected/hard/soft 及 over 标记。
        Raises:
            无。
        """
        output_reserve = (
            self.output_reserve_tokens
            if output_reserve_tokens is None
            else output_reserve_tokens
        )
        soft_buffer = (
            self.soft_buffer_tokens
            if soft_buffer_tokens is None
            else soft_buffer_tokens
        )
        projected = (
            self.token_estimator.estimate_messages(messages)
            + self.token_estimator.estimate_tools(tools or [])
        )

        if max_input_tokens is None:
            return BudgetProjection(
                projected_input_tokens=projected,
                output_reserve_tokens=output_reserve,
                hard_input_limit=None,
                soft_input_limit=None,
                is_hard_over=False,
                is_soft_over=False,
            )

        usable = max_input_tokens - output_reserve
        soft_limit = max(0, usable - soft_buffer)

        return BudgetProjection(
            projected_input_tokens=projected,
            output_reserve_tokens=output_reserve,
            hard_input_limit=max_input_tokens,
            soft_input_limit=soft_limit,
            is_hard_over=projected > usable,
            is_soft_over=projected > soft_limit,
        )
