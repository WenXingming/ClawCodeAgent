"""执行模型调用前的上下文预算预检与投影估算。

本模块负责在真正发起模型调用之前，根据消息与工具定义估算输入 token，并生成统一的预算快照。上层运行时会根据该快照判断是否继续、是否触发 snip，或是否需要进入 compact 等后续流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from context.context_token_estimator import ContextTokenEstimator


@dataclass(frozen=True)
class ContextTokenBudgetSnapshot:
    """描述一次 token 预算预检的结果快照。

    该对象由 `ContextTokenBudgetEvaluator.evaluate()` 在模型调用前生成，集中表达本轮请求的输入投影、输出预留以及软硬阈值命中情况，供上层运行时直接消费。
    """

    projected_input_tokens: int  # int：预估的本次输入 token 数量。
    output_reserve_tokens: int  # int：为模型输出预留的 token 数量。
    hard_input_limit: int | None  # int | None：硬限制阈值；超过后应直接阻断调用。
    soft_input_limit: int | None  # int | None：软限制阈值；超过后通常触发上下文瘦身。
    is_hard_over: bool  # bool：当前投影是否已经超过硬限制。
    is_soft_over: bool  # bool：当前投影是否已经超过软限制。


OUTPUT_RESERVE_TOKENS: int = 4_096  # 默认给模型输出预留的 token 数量。
SOFT_BUFFER_TOKENS: int = 13_000  # 默认从硬限制里再扣除的软缓冲区大小。


@dataclass(frozen=True)
class ContextTokenBudgetEvaluator:
    """基于 token 估算结果生成预算快照。

    典型工作流如下：
    1. 运行时在真正调用模型前准备消息与工具定义。
    2. 调用 `evaluate()` 生成统一的 `ContextTokenBudgetSnapshot`。
    3. 上层根据 `is_soft_over` 与 `is_hard_over` 决定是继续调用、先做 snip，还是直接阻断。
    """

    token_estimator: ContextTokenEstimator = field(default_factory=ContextTokenEstimator)  # ContextTokenEstimator：用于估算消息与工具定义的 token 开销。
    output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS  # int：默认预留给模型输出的 token 数。
    soft_buffer_tokens: int = SOFT_BUFFER_TOKENS  # int：默认用于计算软限制的缓冲 token 数。

    def evaluate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_input_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        soft_buffer_tokens: int | None = None,
    ) -> ContextTokenBudgetSnapshot:
        """预检本次调用是否超预算。

        流程：
        1. 估算消息+工具定义的输入token总数（projected）
        2. 若无max_input_tokens限制，返回projected但不标记超限
        3. 若有限制，计算硬限制可用空间(usable)和软限制空间(soft_limit)
        4. 对比projected与两个限制，标记是否超限

        Args:
            messages (list[dict[str, Any]]): 当前待发送给模型的消息列表。
            tools (list[dict[str, Any]] | None): 当前可用工具定义列表；为 None 时按空列表处理。
            max_input_tokens (int | None): 模型输入侧允许使用的最大 token 预算；为 None 表示不设上限。
            output_reserve_tokens (int | None): 本轮额外指定的输出预留 token；为 None 时使用实例默认值。
            soft_buffer_tokens (int | None): 本轮额外指定的软缓冲 token；为 None 时使用实例默认值。
        Returns:
            ContextTokenBudgetSnapshot: 预算预检结果快照，包含投影值、限制值与超限标志。
        """
        output_reserve = (
            self.output_reserve_tokens
            if output_reserve_tokens is None
            else output_reserve_tokens
        )
        soft_buffer = self.soft_buffer_tokens if soft_buffer_tokens is None else soft_buffer_tokens
        projected = (
            self.token_estimator.estimate_messages(messages)
            + self.token_estimator.estimate_tools(tools or [])
        )

        if max_input_tokens is None:
            return ContextTokenBudgetSnapshot(
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

        return ContextTokenBudgetSnapshot(
            projected_input_tokens=projected,
            output_reserve_tokens=output_reserve,
            hard_input_limit=hard_limit,
            soft_input_limit=soft_limit,
            is_hard_over=projected > usable,
            is_soft_over=projected > soft_limit,
        )