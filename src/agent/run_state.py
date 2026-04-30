"""维护单次 agent run/resume 调用期间的动态运行态。

AgentRunState 把 turn 计数、usage 增量、事件、预算快照、临时工具表、
工具调用计数和 MCP capability window 从主循环的内部变量中收口到
一个可变对象里，使主循环和编排器围绕同一个运行态推进。
该模块不依赖任何其他领域包，只依赖 core_contracts 外部合约。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core_contracts.context import BudgetProjection
from core_contracts.messaging import ToolCall, ToolExecutionResult
from core_contracts.primitives import JSONDict
from core_contracts.session import AgentSessionState
from core_contracts.primitives import TokenUsage
from core_contracts.tools import ToolDescriptor


@dataclass
class AgentRunState:
    """统一承载 agent 主流程中的动态运行状态。

    核心内容：
    1. turn 计数和 session 标识；turn_index / turns_total；
    2. token usage 增量跟踪；usage_delta / usage_total；
    3. cost 基线和当前增量（不直接存储浮点结果）；
    4. 会话消息 / transcript 读写委托给 session_state；
    5. MCP capability window（shortlist + 物化句柄）的汩时维护。
    """

    session_state: AgentSessionState  # AgentSessionState: 会话消息列表与 transcript 的封装对象。
    session_id: str  # str: 本次 run/resume 绑定的会话唯一标识。
    turns_offset: int = 0  # int: 历史基线 turn 数（续跡时从快照加载）。
    usage_baseline: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage: token 历史基线（续跡时加载）。
    cost_baseline: float = 0.0  # float: 成本历史基线（续跡时加载），单位美元。
    tool_call_count: int = 0  # int: 本 run/resume + 历史累计的工具调用次数。
    mcp_capability_shortlist: list[JSONDict] = field(default_factory=list)  # list[JSONDict]: MCP 搜索结果短列表缓存。
    materialized_mcp_capability_handles: list[str] = field(default_factory=list)  # list[str]: 当前需物化的 capability handle 列表。
    final_output: str = ''  # str: 模型最后一轮的文本输出。
    stop_reason: str = 'max_turns'  # str: 最终停止原因标识，默认为 'max_turns'。
    turn_index: int = 0  # int: 当前正在执行的 turn 序号（从 1 开始）。
    turns_this_run: int = 0  # int: 本次 run/resume 调用中新增的 turn 数。
    model_call_count: int = 0  # int: 本次 run/resume 已发生的模型调用次数。
    usage_delta: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage: 本次 run/resume 的 token 增量统计。
    events: list[JSONDict] = field(default_factory=list)  # list[JSONDict]: 本次 run/resume 产生的结构化事件列表。
    token_budget_snapshot: BudgetProjection | None = field(default_factory=lambda: None)  # BudgetProjection | None: 本轮模型调用前的 token 预算快照。
    effective_tool_registry: dict[str, ToolDescriptor] = field(default_factory=dict)  # dict[str, ToolDescriptor]: 当前 turn 对模型可见的完整工具注册表。

    @classmethod
    def for_new_session(
        cls,
        *,
        session_state: AgentSessionState,
        session_id: str,
    ) -> 'AgentRunState':
        """为全新会话创建运行态。
        Args:
            session_state (AgentSessionState): 初始为空的会话状态对象。
            session_id (str): 本次会话的唯一标识。
        Returns:
            AgentRunState: 全部基线为零的初始运行态。
        Raises:
            无。
        """
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
        """为 resume 场景创建带历史基线的运行态。
        Args:
            session_state (AgentSessionState): 已恢复的会话状态对象。
            session_id (str): 本次 resume 续用的会话 ID。
            turns_offset (int): 历史 turn 基线数。
            usage_baseline (TokenUsage): 历史 token usage 基线。
            cost_baseline (float): 历史成本基线，单位美元。
            tool_call_count (int): 历史工具调用次数基线。
            mcp_capability_shortlist (list | tuple | None): 历史 MCP 能力短列表。
            materialized_mcp_capability_handles (list | tuple | None): 历史物化句柄列表。
        Returns:
            AgentRunState: 带历史基线的初始运行态。
        Raises:
            无。
        """
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
        """标记当前执行到的 turn。
        Args:
            turn_index (int): 本次执行的 turn 序号（1 起）。
        Returns:
            None: 原地更新 turn_index 和 turns_this_run。
        Raises:
            无。
        """
        self.turn_index = turn_index
        self.turns_this_run = turn_index

    @property
    def turns_total(self) -> int:
        """返回历史基线与本次调用累计后的总 turn 数。
        Returns:
            int: turns_offset + turns_this_run。
        """
        return self.turns_offset + self.turns_this_run

    @property
    def usage_total(self) -> TokenUsage:
        """返回历史基线与本次增量叠加后的总 usage。
        Returns:
            TokenUsage: usage_baseline + usage_delta。
        """
        return self.usage_baseline + self.usage_delta

    def set_effective_tool_registry(self, tool_registry: dict[str, ToolDescriptor]) -> None:
        """更新当前 turn 的有效工具表。
        Args:
            tool_registry (dict[str, ToolDescriptor]): 当前 turn 可用工具注册表。
        Returns:
            None: 原地更新 effective_tool_registry。
        Raises:
            无。
        """
        self.effective_tool_registry = dict(tool_registry)

    def record_tool_result(self, tool_call: ToolCall, result: ToolExecutionResult) -> None:
        """把工具结果写入会话，并同步更新工具计数。
        Args:
            tool_call (ToolCall): 刚完成的工具调用对象。
            result (ToolExecutionResult): 标准化工具执行结果。
        Returns:
            None: 原地更新 session_state 和 tool_call_count。
        Raises:
            无。
        """
        self.session_state.append_tool_result(tool_call, result)
        self.tool_call_count += 1

    def update_mcp_capability_window(
        self,
        *,
        shortlist: list[JSONDict] | tuple[JSONDict, ...],
        materialized_handles: list[str] | tuple[str, ...],
    ) -> None:
        """整体替换当前运行态缓存的 capability shortlist 与物化窗口。
        Args:
            shortlist (list | tuple): 新的 MCP capability 短列表（内敘多字典）。
            materialized_handles (list | tuple): 新的需物化 capability handle 列表。
        Returns:
            None: 原地更新 mcp_capability_shortlist 和 materialized_mcp_capability_handles。
        Raises:
            无。
        """
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
        """返回 capability shortlist 的只读副本。
        Returns:
            tuple[JSONDict, ...]: shortlist 的不可变密拷贝。
        """
        return tuple(dict(item) for item in self.mcp_capability_shortlist)

    def materialized_mcp_capabilities(self) -> tuple[str, ...]:
        """返回当前需物化的 capability handle 只读视图。
        Returns:
            tuple[str, ...]: 物化句柄的不可变密拷贝。
        """
        return tuple(self.materialized_mcp_capability_handles)