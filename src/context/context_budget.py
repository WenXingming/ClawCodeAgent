"""ISSUE-009 Token Budget 预检与投影估算。

本模块提供两层可复用能力：
1. `ContextTokenEstimator`：按统一启发式口径估算 messages/tools 的输入 token。
2. `ContextBudgetEvaluator`：基于估算结果生成 `TokenBudgetSnapshot`，供 runtime 与控制面复用。

文件内定义按“公共对象优先，再顺着第一次调用链往下读”的顺序组织。当前主阅读链为：

`ContextBudgetEvaluator.evaluate()`
-> `ContextTokenEstimator.estimate_messages()`
-> `ContextTokenEstimator.estimate_message()`
-> `ContextTokenEstimator._estimate_text_tokens()`
-> `ContextTokenEstimator.estimate_tools()`
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# 为模型输出保留的 token 空间，不计入可用输入预算。
OUTPUT_RESERVE_TOKENS: int = 4_096

# auto-compact 触发缓冲：projected 超过 (hard_limit - OUTPUT_RESERVE - SOFT_BUFFER)
# 时置 is_soft_over=True，由 ISSUE-010/011 决定是否执行 snip / compact。
SOFT_BUFFER_TOKENS: int = 13_000

_CHARS_PER_TOKEN: int = 4   # 估算用字符/token 比率（1 token ≈ 4 chars）。
_MSG_OVERHEAD: int = 4      # 每条消息的结构开销（role 标记、格式字节等）。
_CHAT_BASE: int = 3         # 整个消息列表的基础 token 开销。


@dataclass(frozen=True)
class TokenBudgetSnapshot:
    """描述一次 token 预算预检的结果快照。

    该对象由 `ContextBudgetEvaluator.evaluate()` 生成，供 runtime 在模型调用前
    判断是否继续执行、是否触发 snip，以及是否需要进入 compact 流程。
    """

    projected_input_tokens: int  # int：messages 与 tools 合并后的投影输入 token 数。
    output_reserve_tokens: int  # int：为模型输出保留、不可被输入占用的 token 数。
    hard_input_limit: int | None  # int | None：硬输入上限；None 表示不限制。
    soft_input_limit: int | None  # int | None：触发 snip/compact 的软阈值；None 表示不限制。
    is_hard_over: bool  # bool：是否已经超过硬输入上限。
    is_soft_over: bool  # bool：是否已经超过软阈值。

@dataclass(frozen=True)
class ContextTokenEstimator:
    """按统一启发式规则估算消息与工具定义的输入 token。

    典型工作流如下：
    1. runtime、控制面或上下文治理器把当前 `messages` / `tools` 传入本类。
    2. 本类按 char/4 启发式计算内容 token，并叠加消息结构开销。
    3. `ContextBudgetEvaluator`、`ContextSnipper`、`ContextCompactor` 复用同一估算口径。
    """

    chars_per_token: int = _CHARS_PER_TOKEN  # int：字符到 token 的近似换算比率。
    message_overhead_tokens: int = _MSG_OVERHEAD  # int：每条消息额外计入的结构开销。
    chat_base_tokens: int = _CHAT_BASE  # int：整段消息列表的基础 token 开销。

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """估算整个消息列表的总 token 数。

        Args:
            messages (list[dict[str, Any]]): 待估算的消息列表。

        Returns:
            int: 包含列表级基础开销与各消息开销的总 token 估算值。
        """
        return self.chat_base_tokens + sum(self.estimate_message(message) for message in messages)

    def estimate_message(self, message: dict[str, Any]) -> int:
        """估算单条消息的 token 数。

        Args:
            message (dict[str, Any]): 单条消息对象；`content` 可以是字符串、内容块列表或可序列化值。

        Returns:
            int: 当前消息的 token 估算值，包含 role 与结构开销。
        """
        content = message.get('content', '')
        if isinstance(content, str):
            content_tokens = self._estimate_text_tokens(content) if content else 0
        elif isinstance(content, list):
            content_tokens = 0
            for block in content:
                if isinstance(block, dict):
                    text = block.get('text', '')
                    if isinstance(text, str) and text:
                        content_tokens += self._estimate_text_tokens(text)
                    else:
                        content_tokens += self._estimate_text_tokens(json.dumps(block, ensure_ascii=False))
                else:
                    content_tokens += self._estimate_text_tokens(str(block))
        else:
            content_tokens = self._estimate_text_tokens(json.dumps(content, ensure_ascii=False))

        role_tokens = self._estimate_text_tokens(str(message.get('role', '')))
        return role_tokens + content_tokens + self.message_overhead_tokens

    def estimate_tools(self, tools: list[dict[str, Any]]) -> int:
        """估算工具 schema 占用的 token 数。

        Args:
            tools (list[dict[str, Any]]): 待发送给模型的工具定义列表。

        Returns:
            int: 全部工具定义序列化后的 token 估算值；空列表返回 0。
        """
        if not tools:
            return 0
        serialized = json.dumps(tools, ensure_ascii=False)
        return self._estimate_text_tokens(serialized)

    def _estimate_text_tokens(self, text: str) -> int:
        """基于 char/4 启发式估算字符串的 token 数。

        Args:
            text (str): 待估算的原始文本。

        Returns:
            int: 估算得到的 token 数，最小返回 1。
        """
        return max(1, (len(text) + self.chars_per_token - 1) // self.chars_per_token)


@dataclass(frozen=True)
class ContextBudgetEvaluator:
    """基于 token 估算结果生成预算快照。

    该对象是 runtime 与控制面观测上下文压力的统一入口。调用方只需要传入
    当前消息、工具定义和硬上限，类内部会负责组合估算、计算软/硬阈值，并
    返回结构化的 `TokenBudgetSnapshot`。
    """

    token_estimator: ContextTokenEstimator = field(default_factory=ContextTokenEstimator)  # ContextTokenEstimator：消息与工具 token 的统一估算器。
    output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS  # int：为模型输出保留、不可被输入挤占的 token 数。
    soft_buffer_tokens: int = SOFT_BUFFER_TOKENS  # int：从硬上限中再扣除的软缓冲区，用于提前触发治理动作。

    def evaluate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_input_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        soft_buffer_tokens: int | None = None,
    ) -> TokenBudgetSnapshot:
        """生成 token 预算快照。

        Args:
            messages (list[dict[str, Any]]): 当前轮要发给模型的消息列表。
            tools (list[dict[str, Any]] | None): 当前可用的工具 schema 列表；None 等价于空列表。
            max_input_tokens (int | None): 输入 token 的硬上限；None 表示不限制。
            output_reserve_tokens (int | None): 本次调用临时覆盖的输出预留 token 数；None 表示使用实例默认值。
            soft_buffer_tokens (int | None): 本次调用临时覆盖的软缓冲区 token 数；None 表示使用实例默认值。

        Returns:
            TokenBudgetSnapshot: 包含 projected / soft / hard 阈值与超限标志的预算快照。
        """
        output_reserve = self.output_reserve_tokens if output_reserve_tokens is None else output_reserve_tokens
        soft_buffer = self.soft_buffer_tokens if soft_buffer_tokens is None else soft_buffer_tokens
        projected = self.token_estimator.estimate_messages(messages) + self.token_estimator.estimate_tools(tools or [])

        if max_input_tokens is None:
            return TokenBudgetSnapshot(
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

        return TokenBudgetSnapshot(
            projected_input_tokens=projected,
            output_reserve_tokens=output_reserve,
            hard_input_limit=hard_limit,
            soft_input_limit=soft_limit,
            is_hard_over=projected > usable,
            is_soft_over=projected > soft_limit,
        )
