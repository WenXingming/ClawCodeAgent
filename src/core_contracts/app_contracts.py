"""app 领域跨边界数据契约。

本模块定义从 app 领域流出、供外部调用方使用的稳定数据类型。
遵循零泄漏原则：app 内部实现细节不得出现在此文件中，
外部只需依赖本模块即可完整理解 QueryService 的输入/输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import JSONDict
from .token_usage import TokenUsage


@dataclass(frozen=True)
class QueryServiceConfig:
    """QueryService 的轻量配置集合。

    该配置在创建 QueryService 时传入，控制流式输出中是否附加运行时摘要事件。
    """

    include_runtime_summary_event: bool = True  # bool：是否在 stream_submit 末尾附加 runtime_summary 事件。

    def to_dict(self) -> JSONDict:
        """把配置序列化为字典。

        Args:
            无
        Returns:
            JSONDict: 包含全部配置字段的可序列化字典。
        """
        return {'include_runtime_summary_event': self.include_runtime_summary_event}


@dataclass(frozen=True)
class QueryTurnResult:
    """QueryService 单次 submit / stream_submit 对外暴露的稳定结果。

    该对象跨越 app 边界向外传递，包含单轮交互的完整产出与统计数据。
    外部调用方通过 to_dict() 落盘或跨进程传输。
    """

    prompt: str  # str：本轮用户输入原文。
    output: str  # str：本轮 agent 最终输出文本。
    usage: TokenUsage  # TokenUsage：本轮增量 token 使用量（相对上一轮的差值）。
    usage_total: TokenUsage  # TokenUsage：会话累计 token 使用量。
    stop_reason: str  # str：本轮结束原因标识（completed / budget_stop / slash_command 等）。
    session_id: str | None = None  # str | None：关联的会话唯一标识；首轮为 None。
    session_path: str | None = None  # str | None：本轮会话快照文件路径；无持久化时为 None。
    tool_calls: int = 0  # int：本轮累计工具调用次数。
    total_cost_usd: float = 0.0  # float：本轮累计估算成本（美元）。
    events: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：本轮运行期间记录的结构化事件流。
    transcript: tuple[JSONDict, ...] = ()  # tuple[JSONDict, ...]：本轮完整可审计转录条目。

    def to_dict(self) -> JSONDict:
        """把单轮结果转换为 JSON 字典。

        Args:
            无
        Returns:
            JSONDict: 包含全部字段的可序列化字典。
        """
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
