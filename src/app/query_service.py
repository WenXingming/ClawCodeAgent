"""封装 Agent 的程序化查询门面，提供同步提交、流式提交与累计统计汇总。

本模块是 app 领域的纯内部实现，禁止外部直接导入。
外部须通过 AppGateway.create_query_service() 获取 QueryService 实例，
并通过 core_contracts.outcomes 中的 QueryTurnResult / QueryServiceConfig 引用数据契约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

from agent import AgentGateway as Agent
from core_contracts.outcomes import QueryServiceConfig, QueryTurnResult
from core_contracts.primitives import JSONDict
from core_contracts.outcomes import AgentRunResult
from core_contracts.primitives import TokenUsage


@dataclass
class QueryService:
    """封装 Agent 的程序化查询门面。

    核心工作流：
      1. 外部调用 submit(prompt) 或 stream_submit(prompt)；
      2. 内部根据 session_id 自动在 run / resume 之间切换；
      3. 每轮结果以 QueryTurnResult 返回，同时累计 usage、事件与 transcript 统计；
      4. 可通过 render_summary() 获取当前会话的文本摘要。
    """

    runtime_agent: Agent  # Agent：执行 run / resume 的底层代理实例。
    config: QueryServiceConfig = field(default_factory=QueryServiceConfig)  # QueryServiceConfig：控制流式输出行为的配置。
    session_id: str | None = None  # str | None：当前会话 ID；首次 run 后由结果填充。
    turns: list[QueryTurnResult] = field(default_factory=list)  # list[QueryTurnResult]：按提交顺序记录的全部轮次结果。
    cumulative_usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：会话累计 token 使用量。
    runtime_event_counts: dict[str, int] = field(default_factory=dict)  # dict[str, int]：各事件类型的出现计数。
    runtime_mutation_counts: dict[str, int] = field(default_factory=dict)  # dict[str, int]：write_file / edit_file 等变更操作计数。
    runtime_group_status_counts: dict[str, int] = field(default_factory=dict)  # dict[str, int]：delegate_group_complete 状态计数。
    runtime_child_stop_reason_counts: dict[str, int] = field(default_factory=dict)  # dict[str, int]：子 agent 停止原因计数。
    runtime_resumed_children: int = 0  # int：本会话中已恢复的子 agent 数量。
    runtime_lineage_stats: dict[str, int] = field(default_factory=dict)  # dict[str, int]：去重后的血缘统计（unique_groups 等）。
    runtime_transcript_size: int = 0  # int：最近一轮 transcript 条目数量。
    _seen_group_ids: set[str] = field(default_factory=set, init=False, repr=False)  # set[str]：已见 group_id 去重集合（内部）。
    _seen_child_agent_ids: set[str] = field(default_factory=set, init=False, repr=False)  # set[str]：已见子 agent_id 去重集合（内部）。
    _seen_parent_agent_ids: set[str] = field(default_factory=set, init=False, repr=False)  # set[str]：已见父 agent_id 去重集合（内部）。
    _last_turn: QueryTurnResult | None = field(default=None, init=False, repr=False)  # QueryTurnResult | None：最近一轮结果缓存（内部）。

    @classmethod
    def from_runtime_agent(
        cls,
        runtime_agent: Agent,
        *,
        config: QueryServiceConfig | None = None,
    ) -> 'QueryService':
        """基于现有 Agent 创建 QueryService。

        Args:
            runtime_agent (Agent): 已构建好的 Agent 实例。
            config (QueryServiceConfig | None): 可选配置；为 None 时使用默认配置。
        Returns:
            QueryService: 与 runtime_agent 绑定的新查询服务实例。
        Raises:
            无。
        """
        return cls(runtime_agent=runtime_agent, config=config or QueryServiceConfig())

    def submit(self, prompt: str) -> QueryTurnResult:
        """以同步方式提交一条用户输入并返回单轮结果。

        内部自动在首次 run 与后续 resume 之间切换，并把增量 usage、
        事件流与 transcript 写入累计统计。

        Args:
            prompt (str): 用户本轮输入文本。
        Returns:
            QueryTurnResult: 本轮完整输出与统计数据。
        Raises:
            无（底层 Agent 异常会向上透传）。
        """
        previous_usage_total = self.cumulative_usage
        result = self._submit_runtime_message(prompt)
        turn = QueryTurnResult(
            prompt=prompt,
            output=result.final_output,
            usage=_usage_delta(previous_usage_total, result.usage),
            usage_total=result.usage,
            stop_reason=result.stop_reason or 'completed',
            session_id=result.session_id,
            session_path=result.session_path,
            tool_calls=result.tool_calls,
            total_cost_usd=result.total_cost_usd,
            events=tuple(dict(item) for item in result.events),
            transcript=tuple(dict(item) for item in result.transcript),
        )
        self._record_turn(turn)
        return turn

    def stream_submit(self, prompt: str) -> Generator[JSONDict, None, None]:
        """以流式事件形式提交一条用户输入。

        生成顺序：message_start → 各 run 事件 → [runtime_summary] → message_stop。
        实际 Agent 执行在 submit() 中同步完成，本方法仅对结果做格式化展开。

        Args:
            prompt (str): 用户本轮输入文本。
        Returns:
            Generator[JSONDict, None, None]: 逐个产出结构化事件字典的生成器。
        Raises:
            无（底层 Agent 异常会向上透传）。
        """
        yield {
            'type': 'message_start',
            'prompt': prompt,
            'session_id': self.session_id,
        }
        turn = self.submit(prompt)
        for event in turn.events:
            yield dict(event)
        if self.config.include_runtime_summary_event:
            yield self._runtime_summary_event()
        yield {
            'type': 'message_stop',
            'stop_reason': turn.stop_reason,
            'session_id': turn.session_id,
            'session_path': turn.session_path,
            'usage': turn.usage.to_dict(),
            'usage_total': turn.usage_total.to_dict(),
            'transcript_size': len(turn.transcript),
        }

    def persist_session(self) -> str:
        """返回最近一次提交已落盘的 session 文件路径。

        Args:
            无
        Returns:
            str: 会话快照文件的绝对路径字符串。
        Raises:
            ValueError: 当尚未执行过任何提交或会话未持久化时抛出。
        """
        if self._last_turn is None or not self._last_turn.session_path:
            raise ValueError('No persisted session is available yet')
        return self._last_turn.session_path

    def render_summary(self) -> str:
        """渲染当前 QueryService 的累计运行摘要为多行文本。

        Args:
            无
        Returns:
            str: Markdown 格式的统计摘要字符串。
        Raises:
            无。
        """
        lines = [
            '# Query Service Summary',
            '',
            f'- Session id: {self.session_id or "none"}',
            f'- Submitted turns: {len(self.turns)}',
            f'- Total input tokens: {self.cumulative_usage.input_tokens}',
            f'- Total output tokens: {self.cumulative_usage.output_tokens}',
            f'- Runtime transcript size: {self.runtime_transcript_size}',
        ]
        if self._last_turn is not None:
            lines.extend(
                [
                    f'- Last stop reason: {self._last_turn.stop_reason}',
                    f'- Last tool calls: {self._last_turn.tool_calls}',
                    f'- Last session path: {self._last_turn.session_path or "none"}',
                ]
            )
        if self.runtime_event_counts:
            lines.extend(['', '## Runtime Events'])
            lines.extend(f'- {name}={count}' for name, count in sorted(self.runtime_event_counts.items()))
        if self.runtime_mutation_counts:
            lines.extend(['', '## Runtime Mutations'])
            lines.extend(f'- {name}={count}' for name, count in sorted(self.runtime_mutation_counts.items()))
        if self.runtime_group_status_counts or self.runtime_child_stop_reason_counts:
            lines.extend(['', '## Runtime Orchestration'])
            if self.runtime_group_status_counts:
                lines.extend(
                    f'- group_status:{name}={count}'
                    for name, count in sorted(self.runtime_group_status_counts.items())
                )
            if self.runtime_child_stop_reason_counts:
                lines.extend(
                    f'- child_stop:{name}={count}'
                    for name, count in sorted(self.runtime_child_stop_reason_counts.items())
                )
            if self.runtime_resumed_children:
                lines.append(f'- resumed_children={self.runtime_resumed_children}')
        if self.runtime_lineage_stats:
            lines.extend(['', '## Runtime Lineage'])
            lines.extend(f'- {name}={count}' for name, count in sorted(self.runtime_lineage_stats.items()))
        return '\n'.join(lines)

    # ── 私有辅助：按深度优先调用顺序排列 ────────────────────────────────────

    def _submit_runtime_message(self, prompt: str) -> AgentRunResult:
        """在 runtime agent 模式下执行一次 run 或 resume。

        首次调用（_last_turn 为 None 或无 session_id）走 run 分支；
        后续调用从持久化存储中加载快照并走 resume 分支。

        Args:
            prompt (str): 用户本轮输入文本。
        Returns:
            AgentRunResult: 底层 Agent 产出的完整运行结果。
        Raises:
            无（底层异常向上透传）。
        """
        if self._last_turn is None or not self._last_turn.session_id:
            return self.runtime_agent.run(prompt)
        stored = self.runtime_agent.session_manager.load_session(self._last_turn.session_id)
        return self.runtime_agent.resume(prompt, stored)

    def _record_turn(self, turn: QueryTurnResult) -> None:
        """把单轮结果写入 QueryService 累计状态。

        更新 turns 列表、session_id、cumulative_usage 以及运行时统计。

        Args:
            turn (QueryTurnResult): 本轮已完成的结果对象。
        Returns:
            None
        Raises:
            无。
        """
        self.turns.append(turn)
        self._last_turn = turn
        self.session_id = turn.session_id
        self.cumulative_usage = turn.usage_total
        self.runtime_transcript_size = len(turn.transcript)
        self._record_runtime_events(turn.events)
        self._record_runtime_transcript(turn.transcript)

    def _record_runtime_events(self, events: tuple[JSONDict, ...]) -> None:
        """累计运行事件级统计，包含 delegate 编排相关的细分计数。

        Args:
            events (tuple[JSONDict, ...]): 本轮事件流。
        Returns:
            None
        Raises:
            无。
        """
        for event in events:
            event_type = event.get('type')
            if not isinstance(event_type, str) or not event_type:
                continue
            self.runtime_event_counts[event_type] = self.runtime_event_counts.get(event_type, 0) + 1

            if event_type == 'delegate_group_complete':
                status = event.get('status')
                if not isinstance(status, str) and isinstance(event.get('summary'), dict):
                    status = event['summary'].get('status')
                if isinstance(status, str) and status:
                    self.runtime_group_status_counts[status] = (
                        self.runtime_group_status_counts.get(status, 0) + 1
                    )
            elif event_type in {'delegate_child_complete', 'delegate_child_skipped'}:
                stop_reason = event.get('stop_reason', event.get('reason'))
                if isinstance(stop_reason, str) and stop_reason:
                    self.runtime_child_stop_reason_counts[stop_reason] = (
                        self.runtime_child_stop_reason_counts.get(stop_reason, 0) + 1
                    )
            elif event_type == 'delegate_child_start':
                if event.get('resumed_from_session_id'):
                    self.runtime_resumed_children += 1

    def _record_runtime_transcript(self, transcript: tuple[JSONDict, ...]) -> None:
        """从 transcript 中提取 mutation 与血缘 lineage 统计。

        Args:
            transcript (tuple[JSONDict, ...]): 本轮完整转录条目。
        Returns:
            None
        Raises:
            无。
        """
        for entry in transcript:
            if not isinstance(entry, dict):
                continue
            metadata = entry.get('metadata')
            if not isinstance(metadata, dict):
                continue

            action = metadata.get('action')
            if action in {'write_file', 'edit_file'}:
                self.runtime_mutation_counts[action] = self.runtime_mutation_counts.get(action, 0) + 1

            lineage = metadata.get('lineage')
            if not isinstance(lineage, dict):
                continue
            group_id = lineage.get('group_id')
            if isinstance(group_id, str) and group_id:
                self._seen_group_ids.add(group_id)
            parent_agent_id = lineage.get('parent_agent_id')
            if isinstance(parent_agent_id, str) and parent_agent_id:
                self._seen_parent_agent_ids.add(parent_agent_id)
            child_agent_ids = lineage.get('child_agent_ids')
            if isinstance(child_agent_ids, list):
                for child_agent_id in child_agent_ids:
                    if isinstance(child_agent_id, str) and child_agent_id:
                        self._seen_child_agent_ids.add(child_agent_id)
            self.runtime_lineage_stats = {
                'unique_groups': len(self._seen_group_ids),
                'unique_parent_agents': len(self._seen_parent_agent_ids),
                'unique_child_agents': len(self._seen_child_agent_ids),
            }

    def _runtime_summary_event(self) -> JSONDict:
        """构造当前累计统计快照的 runtime_summary 事件字典。

        Args:
            无
        Returns:
            JSONDict: 包含所有运行时统计字段的事件字典，type 固定为 'runtime_summary'。
        Raises:
            无。
        """
        return {
            'type': 'runtime_summary',
            'runtime_event_counts': dict(self.runtime_event_counts),
            'runtime_mutation_counts': dict(self.runtime_mutation_counts),
            'runtime_group_status_counts': dict(self.runtime_group_status_counts),
            'runtime_child_stop_reason_counts': dict(self.runtime_child_stop_reason_counts),
            'runtime_resumed_children': self.runtime_resumed_children,
            'runtime_lineage_stats': dict(self.runtime_lineage_stats),
            'runtime_transcript_size': self.runtime_transcript_size,
        }


def _usage_delta(previous: TokenUsage, current: TokenUsage) -> TokenUsage:
    """计算当前累计 usage 相对上一轮的 token 增量。

    所有字段均以 max(delta, 0) 钳制，避免在 token 计数回退场景下产生负值。

    Args:
        previous (TokenUsage): 上一轮结束后的累计 usage 快照。
        current (TokenUsage): 本轮结束后的累计 usage 快照。
    Returns:
        TokenUsage: 各字段均为非负增量值的新 TokenUsage 对象。
    Raises:
        无。
    """
    return TokenUsage(
        input_tokens=max(current.input_tokens - previous.input_tokens, 0),
        output_tokens=max(current.output_tokens - previous.output_tokens, 0),
        cache_creation_input_tokens=max(
            current.cache_creation_input_tokens - previous.cache_creation_input_tokens,
            0,
        ),
        cache_read_input_tokens=max(
            current.cache_read_input_tokens - previous.cache_read_input_tokens,
            0,
        ),
        reasoning_tokens=max(current.reasoning_tokens - previous.reasoning_tokens, 0),
    )


