"""提供面向上层交互的 runtime facade 与累计统计汇总。"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent import Agent
from core_contracts.protocol import JSONDict
from core_contracts.run_result import AgentRunResult
from core_contracts.token_usage import TokenUsage


@dataclass(frozen=True)
class QueryServiceConfig:
    """QueryService 的轻量配置集合。"""

    include_runtime_summary_event: bool = True


@dataclass(frozen=True)
class QueryTurnResult:
    """QueryService 单次 submit / stream_submit 对外暴露的稳定结果。"""

    prompt: str
    output: str
    usage: TokenUsage
    usage_total: TokenUsage
    stop_reason: str
    session_id: str | None = None
    session_path: str | None = None
    tool_calls: int = 0
    total_cost_usd: float = 0.0
    events: tuple[JSONDict, ...] = ()
    transcript: tuple[JSONDict, ...] = ()

    def to_dict(self) -> JSONDict:
        """把单轮结果转换为字典。"""
        return {
            'prompt': self.prompt,
            'output': self.output,
            'usage': self.usage.to_dict(),
            'usage_total': self.usage_total.to_dict(),
            'stop_reason': self.stop_reason,
            'session_id': self.session_id,
            'session_path': self.session_path,
            'tool_calls': self.tool_calls,
            'total_cost_usd': self.total_cost_usd,
            'events': [dict(item) for item in self.events],
            'transcript': [dict(item) for item in self.transcript],
        }


@dataclass
class QueryService:
    """封装 Agent 的上层交互门面。"""

    runtime_agent: Agent
    config: QueryServiceConfig = field(default_factory=QueryServiceConfig)
    session_id: str | None = None
    turns: list[QueryTurnResult] = field(default_factory=list)
    cumulative_usage: TokenUsage = field(default_factory=TokenUsage)
    runtime_event_counts: dict[str, int] = field(default_factory=dict)
    runtime_mutation_counts: dict[str, int] = field(default_factory=dict)
    runtime_group_status_counts: dict[str, int] = field(default_factory=dict)
    runtime_child_stop_reason_counts: dict[str, int] = field(default_factory=dict)
    runtime_resumed_children: int = 0
    runtime_lineage_stats: dict[str, int] = field(default_factory=dict)
    runtime_transcript_size: int = 0
    _seen_group_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _seen_child_agent_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _seen_parent_agent_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _last_turn: QueryTurnResult | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_runtime_agent(
        cls,
        runtime_agent: Agent,
        *,
        config: QueryServiceConfig | None = None,
    ) -> 'QueryService':
        """基于现有 Agent 创建 QueryService。"""
        return cls(runtime_agent=runtime_agent, config=config or QueryServiceConfig())

    def submit(self, prompt: str) -> QueryTurnResult:
        """以同步方式提交一条用户输入。"""
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

    def stream_submit(self, prompt: str):
        """以流式事件形式提交一条用户输入。"""
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
        """返回最近一次提交已落盘的 session 文件路径。"""
        if self._last_turn is None or not self._last_turn.session_path:
            raise ValueError('No persisted session is available yet')
        return self._last_turn.session_path

    def render_summary(self) -> str:
        """渲染当前 QueryService 的累计运行摘要。"""
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

    def _submit_runtime_message(self, prompt: str) -> AgentRunResult:
        """在 runtime agent 模式下执行一次 run 或 resume。"""
        if self._last_turn is None or not self._last_turn.session_id:
            return self.runtime_agent.run(prompt)
        stored = self.runtime_agent.session_store.load(self._last_turn.session_id)
        return self.runtime_agent.resume(prompt, stored)

    def _record_turn(self, turn: QueryTurnResult) -> None:
        """把单轮结果写入 QueryService 累计状态。"""
        self.turns.append(turn)
        self._last_turn = turn
        self.session_id = turn.session_id
        self.cumulative_usage = turn.usage_total
        self.runtime_transcript_size = len(turn.transcript)
        self._record_runtime_events(turn.events)
        self._record_runtime_transcript(turn.transcript)

    def _record_runtime_events(self, events: tuple[JSONDict, ...]) -> None:
        """累计运行事件级统计。"""
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
        """从 transcript 中提取 mutation 与 lineage 统计。"""
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
        """构造当前累计统计的 summary 事件。"""
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
    """计算当前累计 usage 相对上一轮的增量。"""
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
