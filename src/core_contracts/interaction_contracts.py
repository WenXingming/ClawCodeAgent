"""interaction 领域跨模块共享契约。

本模块集中定义交互层对外可见的数据对象：
1. EnvironmentLoadSummary: 启动阶段环境加载统计。
2. SessionSummary: 会话结束摘要快照。
3. SlashAutocompleteEntry: slash 自动补全条目。
4. SlashCommandContext: slash 分发上下文。
5. SlashCommandResult: slash 分流处理结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from core_contracts.budget import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict
from core_contracts.runtime_policy import ContextPolicy, WorkspaceScope
from core_contracts.session_contracts import AgentSessionState
from core_contracts.tools_contracts import ToolDescriptor


@dataclass(frozen=True)
class EnvironmentLoadSummary:
    """描述交互式启动阶段已发现的环境加载结果。"""

    mcp_servers: int = 0
    plugins: int = 0
    hook_policies: int = 0
    search_providers: int = 0
    load_errors: int = 0

    def render_line(self) -> str:
        """渲染单行环境摘要文本。
        Args:
            无
        Returns:
            str: 可直接输出的一行摘要；无正向统计时返回空字符串。
        Raises:
            无。
        """
        parts = self._build_summary_parts()
        if not parts:
            return ''
        return f'Environment loaded: {", ".join(parts)}'

    def _build_summary_parts(self) -> tuple[str, ...]:
        """按固定顺序构建环境摘要片段。
        Args:
            无
        Returns:
            tuple[str, ...]: 已格式化摘要片段元组。
        Raises:
            无。
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
        Raises:
            无。
        """
        if count <= 0:
            return
        noun = singular if count == 1 else plural
        parts.append(f'{count} {noun}')


@dataclass(frozen=True)
class SessionSummary:
    """表示一次 CLI 交互结束时的只读汇总快照。"""

    session_id: str | None = None
    tool_calls: int = 0
    tool_successes: int = 0
    tool_failures: int = 0
    wall_time_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        """返回工具调用成功率。
        Args:
            无
        Returns:
            float: 成功率，tool_calls 为 0 时返回 0.0。
        Raises:
            无。
        """
        if self.tool_calls <= 0:
            return 0.0
        return self.tool_successes / self.tool_calls


@dataclass(frozen=True)
class SlashAutocompleteEntry:
    """描述一个可补全的 slash 命令项。"""

    name: str
    description: str


@dataclass(frozen=True)
class SlashCommandContext:
    """封装 slash 命令执行期间所需的只读上下文。"""

    session_state: AgentSessionState
    session_id: str
    turns_offset: int
    tool_call_count: int
    workspace_scope: WorkspaceScope
    context_policy: ContextPolicy
    permissions: ToolPermissionPolicy
    budget_config: BudgetConfig
    model_config: ModelConfig
    tool_registry: Mapping[str, ToolDescriptor]
    plugin_summary: str = ''


@dataclass(frozen=True)
class SlashCommandResult:
    """描述一次 slash 分流后的处理结果。"""

    handled: bool
    continue_query: bool
    command_name: str = ''
    output: str = ''
    prompt: str | None = None
    replacement_session_state: AgentSessionState | None = None
    fork_session: bool = False
    metadata: JSONDict = field(default_factory=dict)
