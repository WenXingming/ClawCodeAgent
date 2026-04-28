"""定义代理会话的持久化快照契约与恢复规则。

本模块描述会话快照的稳定落盘结构，并保证以下行为：
1. 与既有 JSON 字段命名保持兼容。
2. 对可选字段缺失提供安全默认值。
3. 对关键字段缺失或类型不合法时抛出明确异常。

文件内成员按“公开入口在前、私有兼容辅助在后”的顺序组织，便于沿着 `to_dict()` 与 `from_dict()` 两条主线理解整个落盘协议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core_contracts.config import AgentRuntimeConfig, ModelConfig
from core_contracts.protocol import JSONDict
from core_contracts.token_usage import TokenUsage


@dataclass(frozen=True)
class AgentSessionSnapshot:
    """表示已落盘的代理会话快照。

    该对象是运行态会话写入磁盘后的不可变视图，集中保存恢复会话所需的核心上下文、累计统计和审计信息。典型工作流如下：
    1. 运行结束后由上层构造 `AgentSessionSnapshot`。
    2. 调用 `to_dict()` 生成可序列化的 JSON 载荷。
    3. 读取磁盘文件后调用 `from_dict()` 恢复快照对象。

    类内的私有辅助方法全部围绕 `from_dict()` 的字段兼容与类型兜底服务。
    """

    session_id: str  # 会话的稳定唯一标识，用于文件命名与恢复匹配。
    model_config: ModelConfig  # 本次会话使用的模型配置快照。
    runtime_config: AgentRuntimeConfig  # 本次会话使用的运行配置快照。
    messages: tuple[JSONDict, ...]  # 恢复模型上下文所需的原始消息序列。
    transcript: tuple[JSONDict, ...] = ()  # 用于审计和追踪的转录条目。
    events: tuple[JSONDict, ...] = ()  # 运行过程中产生的事件记录。
    final_output: str = ''  # 本次运行结束时输出给用户的最终文本。
    turns: int = 0  # 会话累计执行的模型轮次数。
    tool_calls: int = 0  # 会话累计执行的工具调用次数。
    usage: TokenUsage = field(default_factory=TokenUsage)  # 会话累计 token 统计。
    total_cost_usd: float = 0.0  # 会话累计估算成本，单位为美元。
    stop_reason: str | None = None  # 本次会话停止的原因标识。
    file_history: tuple[JSONDict, ...] = ()  # 文件操作历史记录。
    scratchpad_directory: str | None = None  # 会话使用的 scratchpad 目录路径。
    mcp_capability_shortlist: tuple[JSONDict, ...] = ()  # 最近一次 MCP capability search 返回的候选目录项。
    materialized_mcp_capability_handles: tuple[str, ...] = ()  # 下一轮需要继续物化的 capability handle 列表。
    schema_version: int = 1  # 落盘协议版本号，用于未来兼容升级。

    def to_dict(self) -> JSONDict:
        """把会话快照转换成可落盘的 JSON 字典。

        Args:
            None
        Returns:
            JSONDict: 包含当前快照全部字段的字典表示，可直接被 JSON 序列化。
        """
        return {
            'schema_version': self.schema_version,
            'session_id': self.session_id,
            'model_config': self.model_config.to_dict(),
            'runtime_config': self.runtime_config.to_dict(),
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
        """从 JSON 字典恢复已落盘的会话快照。

        该方法兼容 snake_case 与 camelCase 字段名，并对非关键字段使用安全默认值。

        Args:
            payload (JSONDict | None): 从磁盘读取并解析得到的原始 JSON 载荷。
        Returns:
            AgentSessionSnapshot: 恢复后的不可变会话快照对象。
        Raises:
            ValueError: 当 `session_id`、`model_config`、`runtime_config` 或 `messages` 等关键字段缺失或类型不符合要求时抛出。
        """
        data = cls._as_dict(payload)
        session_id = cls._as_str(
            cls._first_present(data, 'session_id', 'sessionId', default=''),
            '',
        ).strip()
        if not session_id:
            raise ValueError('AgentSessionSnapshot.session_id is required')

        model_payload = cls._first_present(data, 'model_config', 'modelConfig')
        if not isinstance(model_payload, dict):
            raise ValueError('AgentSessionSnapshot.model_config must be a JSON object')

        runtime_payload = cls._first_present(data, 'runtime_config', 'runtimeConfig')
        if not isinstance(runtime_payload, dict):
            raise ValueError('AgentSessionSnapshot.runtime_config must be a JSON object')

        messages_raw = data.get('messages')
        if not isinstance(messages_raw, list):
            raise ValueError('AgentSessionSnapshot.messages must be a JSON array')

        transcript_raw = cls._first_present(data, 'transcript', default=[])
        events_raw = cls._first_present(data, 'events', default=[])
        file_history_raw = cls._first_present(data, 'file_history', 'fileHistory', default=[])
        mcp_capability_shortlist_raw = cls._first_present(
            data,
            'mcp_capability_shortlist',
            'mcpCapabilityShortlist',
            default=[],
        )
        materialized_handles_raw = cls._first_present(
            data,
            'materialized_mcp_capability_handles',
            'materializedMcpCapabilityHandles',
            default=[],
        )

        if not isinstance(transcript_raw, list):
            transcript_raw = []
        if not isinstance(events_raw, list):
            events_raw = []
        if not isinstance(file_history_raw, list):
            file_history_raw = []
        if not isinstance(mcp_capability_shortlist_raw, list):
            mcp_capability_shortlist_raw = []
        if not isinstance(materialized_handles_raw, list):
            materialized_handles_raw = []

        stop_reason_raw = cls._first_present(data, 'stop_reason', 'stopReason')
        scratchpad_directory_raw = cls._first_present(
            data,
            'scratchpad_directory',
            'scratchpadDirectory',
        )

        return cls(
            session_id=session_id,
            model_config=ModelConfig.from_dict(model_payload),
            runtime_config=AgentRuntimeConfig.from_dict(runtime_payload),
            messages=tuple(item for item in messages_raw if isinstance(item, dict)),
            transcript=tuple(item for item in transcript_raw if isinstance(item, dict)),
            events=tuple(item for item in events_raw if isinstance(item, dict)),
            final_output=cls._as_str(
                cls._first_present(data, 'final_output', 'finalOutput'),
                '',
            ),
            turns=cls._as_int(data.get('turns'), 0),
            tool_calls=cls._as_int(
                cls._first_present(data, 'tool_calls', 'toolCalls'),
                0,
            ),
            usage=TokenUsage.from_dict(data.get('usage')),
            total_cost_usd=cls._as_float(
                cls._first_present(data, 'total_cost_usd', 'totalCostUsd'),
                0.0,
            ),
            stop_reason=(
                cls._as_str(stop_reason_raw)
                if stop_reason_raw is not None
                else None
            ),
            file_history=tuple(item for item in file_history_raw if isinstance(item, dict)),
            scratchpad_directory=(
                cls._as_str(scratchpad_directory_raw)
                if scratchpad_directory_raw is not None
                else None
            ),
            mcp_capability_shortlist=tuple(
                item for item in mcp_capability_shortlist_raw if isinstance(item, dict)
            ),
            materialized_mcp_capability_handles=tuple(
                cls._as_str(item).strip()
                for item in materialized_handles_raw
                if cls._as_str(item).strip()
            ),
            schema_version=cls._as_int(
                cls._first_present(data, 'schema_version', 'schemaVersion', default=1),
                1,
            ),
        )

    @staticmethod
    def _as_dict(value: Any) -> JSONDict:
        """把输入值安全转换成字典。

        Args:
            value (Any): 待转换的原始值。
        Returns:
            JSONDict: 当输入为 dict 时返回其浅拷贝，否则返回空字典。
        """
        if isinstance(value, dict):
            return dict(value)
        return {}

    @staticmethod
    def _first_present(data: JSONDict, *keys: str, default: Any = None) -> Any:
        """按顺序返回第一个存在且非 None 的字段值。

        Args:
            data (JSONDict): 待读取字段的字典对象。
            *keys (str): 依次尝试读取的字段名序列。
            default (Any): 当所有候选字段都不存在或值为 None 时返回的默认值。
        Returns:
            Any: 第一个存在且非 None 的字段值；若都不存在则返回默认值。
        """
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return default

    @staticmethod
    def _as_str(value: Any, default: str = '') -> str:
        """把输入值安全转换成字符串。

        Args:
            value (Any): 待转换的原始值。
            default (str): 输入为 None 时使用的默认字符串。
        Returns:
            str: 转换后的字符串；若输入为 None 则返回默认值。
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
            value (Any): 待转换的原始值。
            default (int): 转换失败、输入为 None 或 bool 时的默认值。
        Returns:
            int: 成功转换后的整数；否则返回默认值。
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
            value (Any): 待转换的原始值。
            default (float): 转换失败、输入为 None 或 bool 时的默认值。
        Returns:
            float: 成功转换后的浮点数；否则返回默认值。
        """
        if isinstance(value, bool) or value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default