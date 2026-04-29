"""CLI 交互会话汇总模型模块。

本模块负责承载一次交互式 CLI 会话中的统计领域对象，包括：
1. SessionSummary：会话结束时的只读摘要快照；
2. SessionInteractionTracker：交互期间的可变累计器与摘要工厂。
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from core_contracts.interaction_contracts import SessionSummary
from core_contracts.run_result import AgentRunResult


@dataclass
class SessionInteractionTracker:
    """维护一次 CLI 交互生命周期内的可变累计统计。

    工作流为：先通过 start() 创建追踪器；在每轮执行后调用 observe_run_result()
    吸收一轮结果中的会话与工具统计；最后通过 to_summary() 产出可渲染的总结对象。
    """

    session_id: str | None = None  # str | None: 当前已知的活动会话 ID。
    started_time: float = 0.0  # float: 统计开始时的 perf_counter 基准值。
    tool_calls: int = 0  # int: 已累计的工具调用总次数。
    tool_successes: int = 0  # int: 已累计的成功工具调用次数。
    tool_failures: int = 0  # int: 已累计的失败工具调用次数。

    @classmethod
    def start(cls, session_id: str | None = None) -> 'SessionInteractionTracker':
        """创建新的交互汇总状态。

        Args:
            session_id (str | None): 初始会话 ID；尚未建立会话时可为 None。
        Returns:
            SessionInteractionTracker: 已记录启动时间的新追踪器实例。
        """
        return cls(session_id=session_id, started_time=perf_counter())

    def observe_run_result(
        self,
        result: AgentRunResult,
        *,
        current_session_id: str | None,
    ) -> None:
        """吸收单轮执行结果中的增量统计。

        Args:
            result (AgentRunResult): 当前轮执行结果，包含会话 ID 与结构化事件列表。
            current_session_id (str | None): 当前已知的活动会话 ID，用于在结果未显式返回 session_id 时回退。
        Returns:
            None: 该方法只更新追踪器内部状态。
        """
        self.update_session_id(result.session_id or current_session_id)
        for event in result.events:
            if event.get('type') != 'tool_result':
                continue
            self.observe_tool_result(ok=bool(event.get('ok')))

    def observe_tool_result(self, *, ok: bool) -> None:
        """累计一次工具结果。

        Args:
            ok (bool): 当前工具调用是否成功。
        Returns:
            None: 该方法只更新内部累计计数。
        """
        self.tool_calls += 1
        if ok:
            self.tool_successes += 1
            return
        self.tool_failures += 1

    def update_session_id(self, session_id: str | None) -> None:
        """刷新最后一个已知的活动 session id。

        Args:
            session_id (str | None): 新观察到的会话 ID；为空时忽略。
        Returns:
            None: 该方法只在存在有效会话 ID 时更新内部状态。
        """
        if session_id:
            self.session_id = session_id

    def to_summary(self) -> SessionSummary:
        """将累计状态投影为可渲染的总结对象。

        Args:
            None: 该方法直接读取当前追踪器状态。
        Returns:
            SessionSummary: 包含会话 ID、工具统计与耗时的总结对象。
        """
        return SessionSummary(
            session_id=self.session_id,
            tool_calls=self.tool_calls,
            tool_successes=self.tool_successes,
            tool_failures=self.tool_failures,
            wall_time_seconds=max(perf_counter() - self.started_time, 0.0),
        )


__all__ = ['SessionSummary', 'SessionInteractionTracker']