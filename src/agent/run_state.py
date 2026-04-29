"""维护单次 agent run/resume 调用期间的动态运行态。"""

from __future__ import annotations

from dataclasses import dataclass, field

from core_contracts.context_contracts import BudgetProjection
from core_contracts.protocol import JSONDict, ToolCall, ToolExecutionResult
from core_contracts.session_contracts import AgentSessionState
from core_contracts.token_usage import TokenUsage
from core_contracts.tools_contracts import ToolDescriptor


@dataclass
class AgentRunState:
    """统一承载 agent 主流程中的动态运行状态。

    该对象把 turn 计数、usage 增量、事件、预算快照、临时工具表、
    工具调用计数和 MCP capability window 从 `Agent` 的局部变量中
    收口到一个可变对象里，让主循环和编排器围绕同一个运行态推进。
    """

    session_state: AgentSessionState
    session_id: str
    turns_offset: int = 0
    usage_baseline: TokenUsage = field(default_factory=TokenUsage)
    cost_baseline: float = 0.0
    tool_call_count: int = 0
    mcp_capability_shortlist: list[JSONDict] = field(default_factory=list)
    materialized_mcp_capability_handles: list[str] = field(default_factory=list)
    final_output: str = ''
    stop_reason: str = 'max_turns'
    turn_index: int = 0
    turns_this_run: int = 0
    model_call_count: int = 0
    usage_delta: TokenUsage = field(default_factory=TokenUsage)
    events: list[JSONDict] = field(default_factory=list)
    token_budget_snapshot: BudgetProjection | None = None
    effective_tool_registry: dict[str, ToolDescriptor] = field(default_factory=dict)

    @classmethod
    def for_new_session(
        cls,
        *,
        session_state: AgentSessionState,
        session_id: str,
    ) -> 'AgentRunState':
        """为全新会话创建运行态。"""
        return cls(
            session_state=session_state,
            session_id=session_id,
        )

    @classmethod
    def for_resumed_session(
        cls,
        *,
        session_state: AgentSessionState,
        session_id: str,
        turns_offset: int,
        usage_baseline: TokenUsage,
        cost_baseline: float,
        tool_call_count: int,
        mcp_capability_shortlist: list[JSONDict] | tuple[JSONDict, ...] | None = None,
        materialized_mcp_capability_handles: list[str] | tuple[str, ...] | None = None,
    ) -> 'AgentRunState':
        """为 resume 场景创建带历史基线的运行态。"""
        return cls(
            session_state=session_state,
            session_id=session_id,
            turns_offset=turns_offset,
            usage_baseline=usage_baseline,
            cost_baseline=cost_baseline,
            tool_call_count=tool_call_count,
            mcp_capability_shortlist=[
                dict(item)
                for item in (mcp_capability_shortlist or ())
                if isinstance(item, dict)
            ],
            materialized_mcp_capability_handles=[
                item.strip()
                for item in (materialized_mcp_capability_handles or ())
                if isinstance(item, str) and item.strip()
            ],
        )

    def begin_turn(self, turn_index: int) -> None:
        """标记当前执行到的 turn。"""
        self.turn_index = turn_index
        self.turns_this_run = turn_index

    @property
    def turns_total(self) -> int:
        """返回历史基线与本次调用累计后的总 turn 数。"""
        return self.turns_offset + self.turns_this_run

    @property
    def usage_total(self) -> TokenUsage:
        """返回历史基线与本次增量叠加后的总 usage。"""
        return self.usage_baseline + self.usage_delta

    def set_effective_tool_registry(self, tool_registry: dict[str, ToolDescriptor]) -> None:
        """更新当前 turn 的有效工具表。"""
        self.effective_tool_registry = dict(tool_registry)

    def record_tool_result(self, tool_call: ToolCall, result: ToolExecutionResult) -> None:
        """把工具结果写入会话，并同步更新工具计数。"""
        self.session_state.append_tool_result(tool_call, result)
        self.tool_call_count += 1

    def update_mcp_capability_window(
        self,
        *,
        shortlist: list[JSONDict] | tuple[JSONDict, ...],
        materialized_handles: list[str] | tuple[str, ...],
    ) -> None:
        """整体替换当前运行态缓存的 capability shortlist 与物化窗口。"""
        self.mcp_capability_shortlist = [
            dict(item)
            for item in shortlist
            if isinstance(item, dict)
        ]
        self.materialized_mcp_capability_handles = [
            item.strip()
            for item in materialized_handles
            if isinstance(item, str) and item.strip()
        ]

    def mcp_capability_candidates(self) -> tuple[JSONDict, ...]:
        """返回 capability shortlist 的只读副本。"""
        return tuple(dict(item) for item in self.mcp_capability_shortlist)

    def materialized_mcp_capabilities(self) -> tuple[str, ...]:
        """返回当前需物化的 capability handle 只读视图。"""
        return tuple(self.materialized_mcp_capability_handles)