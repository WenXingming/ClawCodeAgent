"""会话领域跨模块契约。

本模块集中定义会话恢复与运行态维护所需的跨边界类型：
1. AgentSessionSnapshot: 会话持久化快照（不可变）。
2. AgentSessionState: 单次 run/resume 调用期间的可变消息状态。

这些类型可被 app、agent、interaction、session 等模块直接依赖，
避免从 session 内部实现文件泄漏数据结构。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core_contracts.budget import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict, OneTurnResponse, ToolCall, ToolExecutionResult
from core_contracts.runtime_policy import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from core_contracts.token_usage import TokenUsage


@dataclass(frozen=True)
class AgentSessionSnapshot:
    """表示已落盘的代理会话快照。
    Args:
        session_id (str): 会话的稳定唯一标识。
        model_config (ModelConfig): 本次会话使用的模型配置快照。
        workspace_scope (WorkspaceScope): 本次会话使用的工作区范围快照。
        execution_policy (ExecutionPolicy): 本次会话使用的执行限制快照。
        context_policy (ContextPolicy): 本次会话使用的上下文治理策略快照。
        permissions (ToolPermissionPolicy): 本次会话使用的工具权限快照。
        budget_config (BudgetConfig): 本次会话使用的预算配置快照。
        session_paths (SessionPaths): 本次会话使用的会话路径快照。
        messages (tuple[JSONDict, ...]): 恢复模型上下文所需的消息序列。
    Returns:
        None
    Raises:
        None
    """

    session_id: str
    model_config: ModelConfig
    workspace_scope: WorkspaceScope
    execution_policy: ExecutionPolicy
    context_policy: ContextPolicy
    permissions: ToolPermissionPolicy
    budget_config: BudgetConfig
    session_paths: SessionPaths
    messages: tuple[JSONDict, ...]
    transcript: tuple[JSONDict, ...] = ()
    events: tuple[JSONDict, ...] = ()
    final_output: str = ''
    turns: int = 0
    tool_calls: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    total_cost_usd: float = 0.0
    stop_reason: str | None = None
    file_history: tuple[JSONDict, ...] = ()
    scratchpad_directory: str | None = None
    mcp_capability_shortlist: tuple[JSONDict, ...] = ()
    materialized_mcp_capability_handles: tuple[str, ...] = ()
    schema_version: int = 1

    def to_dict(self) -> JSONDict:
        """把会话快照转换成可落盘的 JSON 字典。
        Args:
            无
        Returns:
            JSONDict: 可直接被 JSON 序列化的字典载荷。
        Raises:
            无。
        """
        return {
            'schema_version': self.schema_version,
            'session_id': self.session_id,
            'model_config': self.model_config.to_dict(),
            'workspace_scope': self.workspace_scope.to_dict(),
            'execution_policy': self.execution_policy.to_dict(),
            'context_policy': self.context_policy.to_dict(),
            'permissions': self.permissions.to_dict(),
            'budget_config': self.budget_config.to_dict(),
            'session_paths': self.session_paths.to_dict(),
            'messages': [dict(item) for item in self.messages],
            'transcript': [dict(item) for item in self.transcript],
            'events': [dict(item) for item in self.events],
            'final_output': self.final_output,
            'turns': self.turns,
            'tool_calls': self.tool_calls,
            'usage': self.usage.to_dict(),
            'total_cost_usd': self.total_cost_usd,
            'stop_reason': self.stop_reason,
            'file_history': [dict(item) for item in self.file_history],
            'scratchpad_directory': self.scratchpad_directory,
            'mcp_capability_shortlist': [dict(item) for item in self.mcp_capability_shortlist],
            'materialized_mcp_capability_handles': list(self.materialized_mcp_capability_handles),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'AgentSessionSnapshot':
        """从 JSON 字典恢复会话快照。
        Args:
            payload (JSONDict | None): 从磁盘读取后的原始 JSON 载荷。
        Returns:
            AgentSessionSnapshot: 恢复后的会话快照对象。
        Raises:
            ValueError: 当关键字段缺失或类型不合法时抛出。
        """
        data = cls._as_dict(payload)
        session_id = cls._as_str(cls._first_present(data, 'session_id', 'sessionId', default=''), '').strip()
        if not session_id:
            raise ValueError('AgentSessionSnapshot.session_id is required')

        model_payload = cls._first_present(data, 'model_config', 'modelConfig')
        if not isinstance(model_payload, dict):
            raise ValueError('AgentSessionSnapshot.model_config must be a JSON object')

        messages_raw = data.get('messages')
        if not isinstance(messages_raw, list):
            raise ValueError('AgentSessionSnapshot.messages must be a JSON array')

        transcript_raw = cls._first_present(data, 'transcript', default=[])
        events_raw = cls._first_present(data, 'events', default=[])
        file_history_raw = cls._first_present(data, 'file_history', 'fileHistory', default=[])
        shortlist_raw = cls._first_present(data, 'mcp_capability_shortlist', 'mcpCapabilityShortlist', default=[])
        handles_raw = cls._first_present(data, 'materialized_mcp_capability_handles', 'materializedMcpCapabilityHandles', default=[])

        if not isinstance(transcript_raw, list):
            transcript_raw = []
        if not isinstance(events_raw, list):
            events_raw = []
        if not isinstance(file_history_raw, list):
            file_history_raw = []
        if not isinstance(shortlist_raw, list):
            shortlist_raw = []
        if not isinstance(handles_raw, list):
            handles_raw = []

        stop_reason_raw = cls._first_present(data, 'stop_reason', 'stopReason')
        scratchpad_raw = cls._first_present(data, 'scratchpad_directory', 'scratchpadDirectory')

        return cls(
            session_id=session_id,
            model_config=ModelConfig.from_dict(model_payload),
            workspace_scope=WorkspaceScope.from_dict(cls._first_present(data, 'workspace_scope', 'workspaceScope', default={})),
            execution_policy=ExecutionPolicy.from_dict(cls._first_present(data, 'execution_policy', 'executionPolicy', default={})),
            context_policy=ContextPolicy.from_dict(cls._first_present(data, 'context_policy', 'contextPolicy', default={})),
            permissions=ToolPermissionPolicy.from_dict(cls._first_present(data, 'permissions', default={})),
            budget_config=BudgetConfig.from_dict(cls._first_present(data, 'budget_config', 'budgetConfig', default={})),
            session_paths=SessionPaths.from_dict(cls._first_present(data, 'session_paths', 'sessionPaths', default={})),
            messages=tuple(item for item in messages_raw if isinstance(item, dict)),
            transcript=tuple(item for item in transcript_raw if isinstance(item, dict)),
            events=tuple(item for item in events_raw if isinstance(item, dict)),
            final_output=cls._as_str(cls._first_present(data, 'final_output', 'finalOutput'), ''),
            turns=cls._as_int(data.get('turns'), 0),
            tool_calls=cls._as_int(cls._first_present(data, 'tool_calls', 'toolCalls'), 0),
            usage=TokenUsage.from_dict(data.get('usage')),
            total_cost_usd=cls._as_float(cls._first_present(data, 'total_cost_usd', 'totalCostUsd'), 0.0),
            stop_reason=(cls._as_str(stop_reason_raw) if stop_reason_raw is not None else None),
            file_history=tuple(item for item in file_history_raw if isinstance(item, dict)),
            scratchpad_directory=(cls._as_str(scratchpad_raw) if scratchpad_raw is not None else None),
            mcp_capability_shortlist=tuple(item for item in shortlist_raw if isinstance(item, dict)),
            materialized_mcp_capability_handles=tuple(
                cls._as_str(item).strip()
                for item in handles_raw
                if cls._as_str(item).strip()
            ),
            schema_version=cls._as_int(cls._first_present(data, 'schema_version', 'schemaVersion', default=1), 1),
        )

    @staticmethod
    def _as_dict(value: Any) -> JSONDict:
        """把输入值安全转换成字典。
        Args:
            value (Any): 待转换值。
        Returns:
            JSONDict: 输入为字典时的浅拷贝，否则返回空字典。
        Raises:
            无。
        """
        if isinstance(value, dict):
            return dict(value)
        return {}

    @staticmethod
    def _first_present(data: JSONDict, *keys: str, default: Any = None) -> Any:
        """按顺序返回第一个存在且非 None 的字段值。
        Args:
            data (JSONDict): 源字典。
            *keys (str): 候选字段名。
            default (Any): 全部缺失时返回的默认值。
        Returns:
            Any: 匹配值或默认值。
        Raises:
            无。
        """
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return default

    @staticmethod
    def _as_str(value: Any, default: str = '') -> str:
        """把输入值安全转换成字符串。
        Args:
            value (Any): 待转换值。
            default (str): value 为 None 时返回值。
        Returns:
            str: 转换结果。
        Raises:
            无。
        """
        if isinstance(value, str):
            return value
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        """把输入值安全转换成整数。
        Args:
            value (Any): 待转换值。
            default (int): 转换失败默认值。
        Returns:
            int: 转换结果。
        Raises:
            无。
        """
        if isinstance(value, bool) or value is None:
            return default
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        """把输入值安全转换成浮点数。
        Args:
            value (Any): 待转换值。
            default (float): 转换失败默认值。
        Returns:
            float: 转换结果。
        Raises:
            无。
        """
        if isinstance(value, bool) or value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


@dataclass
class AgentSessionState:
    """表示代理单次运行期间的可变会话状态。
    Args:
        messages (list[JSONDict]): 发送给模型的上下文消息列表。
        transcript_entries (list[JSONDict]): 可审计的转录条目列表。
    Returns:
        None
    Raises:
        None
    """

    messages: list[JSONDict] = field(default_factory=list)
    transcript_entries: list[JSONDict] = field(default_factory=list)

    @classmethod
    def create(cls, prompt: str) -> 'AgentSessionState':
        """按首条用户输入创建会话状态。
        Args:
            prompt (str): 用户首条输入。
        Returns:
            AgentSessionState: 已初始化状态。
        Raises:
            无。
        """
        session_state = cls()
        session_state.append_user(prompt)
        return session_state

    @classmethod
    def from_persisted(
        cls,
        messages: list[JSONDict],
        transcript: list[JSONDict],
    ) -> 'AgentSessionState':
        """从持久化数据恢复运行态会话。
        Args:
            messages (list[JSONDict]): 持久化消息序列。
            transcript (list[JSONDict]): 持久化转录序列。
        Returns:
            AgentSessionState: 恢复后的运行态对象。
        Raises:
            无。
        """
        if transcript:
            effective_transcript = [dict(item) for item in transcript]
        else:
            effective_transcript = [
                {'role': item['role'], 'content': item.get('content', '')}
                for item in messages
                if isinstance(item, dict) and 'role' in item
            ]
        return cls(
            messages=[dict(item) for item in messages],
            transcript_entries=effective_transcript,
        )

    def append_user(self, prompt: str) -> None:
        """向会话追加一条用户消息。
        Args:
            prompt (str): 用户输入内容。
        Returns:
            None
        Raises:
            无。
        """
        message = {'role': 'user', 'content': prompt}
        self.messages.append(message)
        self.transcript_entries.append(dict(message))

    def append_assistant_turn(self, response: OneTurnResponse) -> None:
        """向会话追加一轮助手响应。
        Args:
            response (OneTurnResponse): 模型返回的一轮响应。
        Returns:
            None
        Raises:
            无。
        """
        assistant_message: JSONDict = {
            'role': 'assistant',
            'content': response.content,
        }
        self.messages.append(assistant_message)
        tool_calls_payload = [self._to_openai_tool_call_payload(call) for call in response.tool_calls]
        if tool_calls_payload:
            assistant_message['tool_calls'] = tool_calls_payload
        self.transcript_entries.append(
            {
                'role': 'assistant',
                'content': response.content,
                'finish_reason': response.finish_reason,
                'tool_calls': [call.to_dict() for call in response.tool_calls],
                'usage': response.usage.to_dict(),
            }
        )

    def append_runtime_message(self, content: str, *, metadata: JSONDict | None = None) -> None:
        """向会话追加一条运行时 system/reminder 消息。
        Args:
            content (str): 写入消息内容。
            metadata (JSONDict | None): 转录元数据。
        Returns:
            None
        Raises:
            无。
        """
        message = {'role': 'system', 'content': content}
        self.messages.append(message)
        transcript_entry: JSONDict = {'role': 'system', 'content': content}
        if metadata:
            transcript_entry['metadata'] = dict(metadata)
        self.transcript_entries.append(transcript_entry)

    def append_tool_result(self, tool_call: ToolCall, result: ToolExecutionResult) -> None:
        """向会话追加一条工具执行结果。
        Args:
            tool_call (ToolCall): 触发调用的工具描述。
            result (ToolExecutionResult): 工具执行结果。
        Returns:
            None
        Raises:
            无。
        """
        tool_message: JSONDict = {
            'role': 'tool',
            'tool_call_id': tool_call.id,
            'name': tool_call.name,
            'content': result.content,
        }
        self.messages.append(tool_message)
        self.transcript_entries.append(
            {
                'role': 'tool',
                'tool_call_id': tool_call.id,
                'tool_name': tool_call.name,
                'content': result.content,
                'ok': result.ok,
                'metadata': dict(result.metadata),
            }
        )

    def to_messages(self) -> list[JSONDict]:
        """导出可继续发送给模型的消息副本。
        Args:
            无
        Returns:
            list[JSONDict]: 消息列表副本。
        Raises:
            无。
        """
        return [dict(item) for item in self.messages]

    def transcript(self) -> tuple[JSONDict, ...]:
        """导出稳定的转录只读视图。
        Args:
            无
        Returns:
            tuple[JSONDict, ...]: 转录条目副本。
        Raises:
            无。
        """
        return tuple(dict(item) for item in self.transcript_entries)

    @staticmethod
    def _to_openai_tool_call_payload(tool_call: ToolCall) -> JSONDict:
        """把内部 ToolCall 转成 OpenAI 兼容 tool_calls 结构。
        Args:
            tool_call (ToolCall): 工具调用对象。
        Returns:
            JSONDict: OpenAI 兼容 tool_call 载荷。
        Raises:
            无。
        """
        return {
            'id': tool_call.id,
            'type': 'function',
            'function': {
                'name': tool_call.name,
                'arguments': json.dumps(tool_call.arguments, ensure_ascii=True),
            },
        }
