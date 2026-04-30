"""会话跨模块契约。

定义 AgentSessionSnapshot (持久化快照) 与 AgentSessionState (运行时可变状态)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ._coercion import _as_dict, _as_float, _as_int, _as_str, _first_present
from .config import (
    BudgetConfig,
    ContextPolicy,
    ExecutionPolicy,
    SessionPaths,
    ToolPermissionPolicy,
    WorkspaceScope,
)
from .messaging import ToolCall, ToolExecutionResult
from .model import ModelConfig
from .primitives import JSONDict, TokenUsage
from .messaging import OneTurnResponse


class SessionContractError(RuntimeError):
    """session 领域异常统一基类。"""


class SessionValidationError(SessionContractError):
    """session 请求参数或会话标识不合法。"""


class SessionNotFoundError(SessionContractError):
    """请求的会话快照不存在。"""


class SessionPersistenceError(SessionContractError):
    """会话快照存储或解析失败。"""


@dataclass(frozen=True)
class SessionSaveRequest:
    """保存会话快照的标准请求契约。"""

    snapshot: 'AgentSessionSnapshot'  # AgentSessionSnapshot：待保存的会话快照对象。


@dataclass(frozen=True)
class SessionSaveResult:
    """保存会话快照的标准结果契约。"""

    session_id: str  # str：会话唯一标识。
    session_path: str  # str：落盘后的会话文件路径。


@dataclass(frozen=True)
class SessionLoadRequest:
    """加载会话快照的标准请求契约。"""

    session_id: str  # str：会话唯一标识。


@dataclass(frozen=True)
class SessionLoadResult:
    """加载会话快照的标准结果契约。"""

    session_id: str  # str：会话唯一标识。
    snapshot: 'AgentSessionSnapshot'  # AgentSessionSnapshot：恢复出的会话快照。


@dataclass(frozen=True)
class SessionStateCreateRequest:
    """创建运行态会话对象的标准请求契约。"""

    prompt: str  # str：首条用户输入。


@dataclass(frozen=True)
class SessionStateResumeRequest:
    """从持久化数据恢复运行态会话对象的标准请求契约。"""

    messages: tuple[JSONDict, ...]  # tuple[JSONDict, ...]：模型消息历史。
    transcript: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：转录历史。


@dataclass(frozen=True)
class AgentSessionSnapshot:
    """表示已落盘的代理会话快照。

    该对象封装完整会话状态的持久化视图，支持 to_dict/from_dict 序列化。
    """

    session_id: str  # str：会话的稳定唯一标识。
    model_config: ModelConfig  # ModelConfig：模型配置快照。
    workspace_scope: WorkspaceScope  # WorkspaceScope：工作区范围快照。
    execution_policy: ExecutionPolicy  # ExecutionPolicy：执行限制快照。
    context_policy: ContextPolicy  # ContextPolicy：上下文治理策略快照。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy：工具权限快照。
    budget_config: BudgetConfig  # BudgetConfig：预算配置快照。
    session_paths: SessionPaths  # SessionPaths：会话路径快照。
    messages: tuple[JSONDict, ...]  # tuple[JSONDict, ...]：恢复模型上下文所需的消息序列。
    transcript: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：可审计转录条目。
    events: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：结构化事件流。
    final_output: str = ''  # str：最终输出文本。
    turns: int = 0  # int：累计 turn 数。
    tool_calls: int = 0  # int：累计工具调用次数。
    usage: TokenUsage = field(default_factory=TokenUsage)  # TokenUsage：累计 token 统计。
    total_cost_usd: float = 0.0  # float：累计成本（美元）。
    stop_reason: str | None = None  # str | None：停止原因。
    file_history: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：文件操作历史。
    scratchpad_directory: str | None = None  # str | None：沙盒目录。
    mcp_capability_shortlist: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：MCP 能力短名单。
    materialized_mcp_capability_handles: tuple[str, ...] = ()  # tuple[str, ...]：已物化的 MCP 能力句柄。
    schema_version: int = 1  # int：快照 schema 版本号。

    def to_dict(self) -> JSONDict:
        """把会话快照转换成可落盘的 JSON 字典。
        Returns:
            JSONDict: 可直接被 JSON 序列化的字典载荷。
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
        data = _as_dict(payload)
        session_id = _as_str(_first_present(data, 'session_id', 'sessionId', default=''), '').strip()
        if not session_id:
            raise ValueError('AgentSessionSnapshot.session_id is required')

        model_payload = _first_present(data, 'model_config', 'modelConfig')
        if not isinstance(model_payload, dict):
            raise ValueError('AgentSessionSnapshot.model_config must be a JSON object')

        messages_raw = data.get('messages')
        if not isinstance(messages_raw, list):
            raise ValueError('AgentSessionSnapshot.messages must be a JSON array')

        transcript_raw = _first_present(data, 'transcript', default=[])
        events_raw = _first_present(data, 'events', default=[])
        file_history_raw = _first_present(data, 'file_history', 'fileHistory', default=[])
        shortlist_raw = _first_present(data, 'mcp_capability_shortlist', 'mcpCapabilityShortlist', default=[])
        handles_raw = _first_present(data, 'materialized_mcp_capability_handles', 'materializedMcpCapabilityHandles', default=[])

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

        stop_reason_raw = _first_present(data, 'stop_reason', 'stopReason')
        scratchpad_raw = _first_present(data, 'scratchpad_directory', 'scratchpadDirectory')

        return cls(
            session_id=session_id,
            model_config=ModelConfig.from_dict(model_payload),
            workspace_scope=WorkspaceScope.from_dict(_first_present(data, 'workspace_scope', 'workspaceScope', default={})),
            execution_policy=ExecutionPolicy.from_dict(_first_present(data, 'execution_policy', 'executionPolicy', default={})),
            context_policy=ContextPolicy.from_dict(_first_present(data, 'context_policy', 'contextPolicy', default={})),
            permissions=ToolPermissionPolicy.from_dict(_first_present(data, 'permissions', default={})),
            budget_config=BudgetConfig.from_dict(_first_present(data, 'budget_config', 'budgetConfig', default={})),
            session_paths=SessionPaths.from_dict(_first_present(data, 'session_paths', 'sessionPaths', default={})),
            messages=tuple(item for item in messages_raw if isinstance(item, dict)),
            transcript=tuple(item for item in transcript_raw if isinstance(item, dict)),
            events=tuple(item for item in events_raw if isinstance(item, dict)),
            final_output=_as_str(_first_present(data, 'final_output', 'finalOutput'), ''),
            turns=_as_int(data.get('turns'), 0),
            tool_calls=_as_int(_first_present(data, 'tool_calls', 'toolCalls'), 0),
            usage=TokenUsage.from_dict(data.get('usage')),
            total_cost_usd=_as_float(_first_present(data, 'total_cost_usd', 'totalCostUsd'), 0.0),
            stop_reason=(_as_str(stop_reason_raw) if stop_reason_raw is not None else None),
            file_history=tuple(item for item in file_history_raw if isinstance(item, dict)),
            scratchpad_directory=(_as_str(scratchpad_raw) if scratchpad_raw is not None else None),
            mcp_capability_shortlist=tuple(item for item in shortlist_raw if isinstance(item, dict)),
            materialized_mcp_capability_handles=tuple(
                stripped for item in handles_raw if (stripped := _as_str(item).strip())
            ),
            schema_version=_as_int(_first_present(data, 'schema_version', 'schemaVersion', default=1), 1),
        )


@dataclass
class AgentSessionState:
    """表示代理单次运行期间的可变会话状态。"""

    messages: list[JSONDict] = field(default_factory=list)  # list[JSONDict]：发送给模型的上下文消息列表。
    transcript_entries: list[JSONDict] = field(default_factory=list)  # list[JSONDict]：可审计的转录条目列表。

    @classmethod
    def create(cls, prompt: str) -> 'AgentSessionState':
        """按首条用户输入创建会话状态。
        Args:
            prompt (str): 用户首条输入。
        Returns:
            AgentSessionState: 已初始化状态。
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
        """
        assistant_message: JSONDict = {'role': 'assistant', 'content': response.content}
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
        Returns:
            list[JSONDict]: 消息列表副本。
        """
        return [dict(item) for item in self.messages]

    def transcript(self) -> tuple[JSONDict, ...]:
        """导出稳定的转录只读视图。
        Returns:
            tuple[JSONDict, ...]: 转录条目副本。
        """
        return tuple(dict(item) for item in self.transcript_entries)

    @staticmethod
    def _to_openai_tool_call_payload(tool_call: ToolCall) -> JSONDict:
        """把内部 ToolCall 转成 OpenAI 兼容 tool_calls 结构。
        Args:
            tool_call (ToolCall): 工具调用对象。
        Returns:
            JSONDict: OpenAI 兼容 tool_call 载荷。
        """
        return {
            'id': tool_call.id,
            'type': 'function',
            'function': {
                'name': tool_call.name,
                'arguments': json.dumps(tool_call.arguments, ensure_ascii=True),
            },
        }
