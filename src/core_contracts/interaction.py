"""交互层跨模块契约。

定义环境加载摘要、会话摘要、slash 命令自动补全与分流处理的跨边界数据对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping, Protocol

from .config import BudgetConfig, ContextPolicy, ToolPermissionPolicy, WorkspaceScope
from .model import ModelConfig
from .primitives import JSONDict
from .session import AgentSessionState
from .tools import ToolDescriptor


@dataclass(frozen=True)
class EnvironmentLoadSummary:
    """描述交互式启动阶段已发现的环境加载结果。"""

    mcp_servers: int = 0  # int：发现的 MCP 服务器数量。
    plugins: int = 0  # int：发现的插件数量。
    hook_policies: int = 0  # int：发现的 hook 策略数量。
    search_providers: int = 0  # int：发现的搜索提供商数量。
    load_errors: int = 0  # int：加载阶段的错误数量。

    def render_line(self) -> str:
        """渲染单行环境摘要文本。
        Returns:
            str: 可直接输出的一行摘要；无正向统计时返回空字符串。
        """
        parts = self._build_summary_parts()
        if not parts:
            return ''
        return f'Environment loaded: {", ".join(parts)}'

    def _build_summary_parts(self) -> tuple[str, ...]:
        """按固定顺序构建环境摘要片段。
        Returns:
            tuple[str, ...]: 已格式化摘要片段元组。
        """
        parts: list[str] = []
        self._append_count_part(parts, self.mcp_servers, singular='MCP server', plural='MCP servers')
        self._append_count_part(parts, self.plugins, singular='plugin', plural='plugins')
        self._append_count_part(parts, self.hook_policies, singular='hook policy', plural='hook policies')
        self._append_count_part(parts, self.search_providers, singular='search provider', plural='search providers')
        self._append_count_part(parts, self.load_errors, singular='load error', plural='load errors')
        return tuple(parts)

    @staticmethod
    def _append_count_part(parts: list[str], count: int, *, singular: str, plural: str) -> None:
        """把单个计数片段追加到摘要列表中。
        Args:
            parts (list[str]): 摘要片段累积列表。
            count (int): 当前统计值。
            singular (str): 单数名词。
            plural (str): 复数名词。
        Returns:
            None
        """
        if count <= 0:
            return
        noun = singular if count == 1 else plural
        parts.append(f'{count} {noun}')


@dataclass(frozen=True)
class SessionSummary:
    """表示一次 CLI 交互结束时的只读汇总快照。"""

    session_id: str | None = None  # str | None：会话 ID。
    tool_calls: int = 0  # int：累计工具调用次数。
    tool_successes: int = 0  # int：成功工具调用次数。
    tool_failures: int = 0  # int：失败工具调用次数。
    wall_time_seconds: float = 0.0  # float：挂钟耗时，单位秒。

    @property
    def success_rate(self) -> float:
        """返回工具调用成功率。
        Returns:
            float: 成功率，tool_calls 为 0 时返回 0.0。
        """
        if self.tool_calls <= 0:
            return 0.0
        return self.tool_successes / self.tool_calls


@dataclass(frozen=True)
class SlashAutocompleteEntry:
    """描述一个可补全的 slash 命令项。"""

    name: str  # str：命令名称。
    description: str  # str：命令说明。


@dataclass(frozen=True)
class SlashCommandContext:
    """封装 slash 命令执行期间所需的只读上下文。"""

    session_state: AgentSessionState  # AgentSessionState：当前会话状态。
    session_id: str  # str：会话 ID。
    turns_offset: int  # int：turn 偏移量。
    tool_call_count: int  # int：累计工具调用次数。
    workspace_scope: WorkspaceScope  # WorkspaceScope：工作区范围。
    context_policy: ContextPolicy  # ContextPolicy：上下文治理策略。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy：工具权限。
    budget_config: BudgetConfig  # BudgetConfig：预算配置。
    model_config: ModelConfig  # ModelConfig：模型配置。
    tool_registry: Mapping[str, ToolDescriptor]  # Mapping[str, ToolDescriptor]：工具注册表。
    plugin_summary: str = ''  # str：插件加载摘要。


@dataclass(frozen=True)
class SlashCommandResult:
    """描述一次 slash 分流后的处理结果。"""

    handled: bool  # bool：命令是否已被处理。
    continue_query: bool  # bool：是否继续发起模型查询。
    command_name: str = ''  # str：匹配到的命令名称。
    output: str = ''  # str：命令执行输出。
    prompt: str | None = None  # str | None：注入的用户 prompt。
    replacement_session_state: AgentSessionState | None = None  # AgentSessionState | None：替换的会话状态。
    fork_session: bool = False  # bool：是否开启新会话。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict：额外元数据。


@dataclass(frozen=True)
class ParsedSlashCommand:
    """表示一次成功解析的 slash 输入。"""

    command_name: str  # str: 规范化后的命令名，不包含前导斜杠。
    arguments: str  # str: 命令后的原始参数文本，保留空格折叠后的用户输入。
    raw_input: str  # str: 用户提交的原始输入，供日志与回显复用。


SlashHandler = Callable[[SlashCommandContext, ParsedSlashCommand], SlashCommandResult]


@dataclass(frozen=True)
class SlashCommandSpec:
    """定义单个 slash 命令的名称、描述与处理器。"""

    names: tuple[str, ...]  # tuple[str, ...]: 当前命令支持的全部名称与别名。
    description: str  # str: 面向 /help 输出的人类可读描述。
    handler: SlashHandler  # SlashHandler: 真正执行业务逻辑的命令处理函数。


@dataclass(frozen=True)
class SlashCommandResolution:
    """表示一次 slash 命令匹配的解析结果。"""

    kind: Literal['exact', 'prefix', 'ambiguous', 'none', 'empty']
    spec: SlashCommandSpec | None = None
    matched_name: str = ''
    candidates: tuple[str, ...] = ()


class SlashDispatcher(Protocol):
    """slash 命令分发器的最小跨域协议。"""

    def dispatch_slash_command(
        self,
        context: SlashCommandContext,
        input_text: str,
    ) -> SlashCommandResult:
        """分发一条 slash 输入并返回处理结果。"""
        ...
