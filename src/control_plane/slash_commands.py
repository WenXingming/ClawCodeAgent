"""Slash 命令控制面模块。

本模块负责三件事：
1. 解析用户输入中的本地 slash 命令。
2. 根据会话与运行时上下文分发到具体命令处理器。
3. 以兼容包装函数的形式对外暴露稳定入口，供 runtime 与测试复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from budget.budget_evaluator import ContextBudgetEvaluator
from core_contracts.config import AgentRuntimeConfig, ModelConfig
from core_contracts.protocol import JSONDict
from session.session_state import AgentSessionState
from tools.agent_tools import AgentTool


@dataclass(frozen=True)
class ParsedSlashCommand:
    """表示一次成功解析的 slash 输入。

    外部通常先通过 SlashCommandDispatcher.parse_slash_command() 获取该对象，
    然后再交给分发器或具体处理器执行。
    """

    command_name: str  # str: 规范化后的命令名，不包含前导斜杠。
    arguments: str  # str: 命令后的原始参数文本，保留空格折叠后的用户输入。
    raw_input: str  # str: 用户提交的原始输入，供日志与回显复用。


@dataclass(frozen=True)
class SlashCommandContext:
    """封装 slash 命令执行期间所需的只读上下文。

    该对象把 session、模型配置、运行时配置与工具注册表解耦后传入控制面，
    使 slash 模块不需要直接依赖 LocalCodingAgent。
    """

    session_state: AgentSessionState  # AgentSessionState: 当前会话内存状态，供命令读取消息与转录历史。
    session_id: str  # str: 当前会话标识，用于状态展示与结果关联。
    turns_offset: int  # int: 历史已完成轮次，供 /status 与 /clear 判断是否已有历史。
    runtime_config: AgentRuntimeConfig  # AgentRuntimeConfig: 当前工作目录、权限与预算等运行配置。
    model_config: ModelConfig  # ModelConfig: 当前模型元数据，供 /status 展示模型名。
    tool_registry: Mapping[str, AgentTool]  # Mapping[str, AgentTool]: 当前已注册工具集合。
    plugin_summary: str = ''  # str: 插件运行时生成的摘要文本，供 /tools 追加展示。


@dataclass(frozen=True)
class SlashCommandResult:
    """描述一次 slash 分流后的处理结果。"""

    handled: bool  # bool: 是否已被 slash 控制面识别并处理。
    continue_query: bool  # bool: 处理后是否还需要继续进入常规模型 query 路径。
    command_name: str = ''  # str: 已识别的命令名；非 slash 或透传时可为空。
    output: str = ''  # str: 本地命令输出文本，由调用方决定如何展示。
    prompt: str | None = None  # str | None: 需要继续写入会话的 prompt，通常为原始用户输入。
    replacement_session_state: AgentSessionState | None = None  # AgentSessionState | None: /clear 等命令返回的新会话状态。
    fork_session: bool = False  # bool: 是否要求上层以 fork 语义生成一个新会话。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict: 额外元数据，用于标注错误码或分支上下文。


SlashHandler = Callable[[SlashCommandContext, ParsedSlashCommand], SlashCommandResult]


@dataclass(frozen=True)
class SlashCommandSpec:
    """定义单个 slash 命令的名称、描述与处理器。"""

    names: tuple[str, ...]  # tuple[str, ...]: 当前命令支持的全部名称与别名。
    description: str  # str: 面向 /help 输出的人类可读描述。
    handler: SlashHandler  # SlashHandler: 真正执行业务逻辑的命令处理函数。


class SlashCommandDispatcher:
    """本地 slash 命令的面向对象分发器。

    工作流分为三步：
    1. parse_slash_command() 把原始输入转换为 ParsedSlashCommand。
    2. dispatch_slash_command() 按命令名查找 SlashCommandSpec 并执行处理器。
    3. 处理器从 SlashCommandContext 读取会话、预算、权限与工具信息，返回 SlashCommandResult。

    外部可直接实例化本类并调用其公有方法；如果需要注入自定义预算评估器，
    可在构造时显式传入。
    """

    def __init__(self, budget_evaluator: ContextBudgetEvaluator | None = None) -> None:
        """初始化 slash 命令分发器。

        Args:
            budget_evaluator (ContextBudgetEvaluator | None): 可选的预算评估器；未提供时创建默认实例。

        Returns:
            None: 该构造函数只负责建立分发器内部状态。
        """
        self._budget_evaluator = budget_evaluator or ContextBudgetEvaluator()  # ContextBudgetEvaluator: /context 使用的 token 预算投影器。
        self._specs = self._build_specs()  # tuple[SlashCommandSpec, ...]: 当前分发器支持的全部命令规格，按帮助输出顺序保存。
        self._spec_index = self._build_spec_index(self._specs)  # dict[str, SlashCommandSpec]: 命令名到规格的查找索引。

    def parse_slash_command(self, input_text: str) -> ParsedSlashCommand | None:
        """从原始输入中提取 slash 命令。

        Args:
            input_text (str): 用户提交的原始输入文本。

        Returns:
            ParsedSlashCommand | None: 成功时返回解析结果；普通 prompt 返回 None。
        """
        stripped = input_text.strip()
        if not stripped.startswith('/'):
            return None

        body = stripped[1:]
        command_name, _, arguments = body.partition(' ')
        return ParsedSlashCommand(
            command_name=command_name.strip().lower(),
            arguments=arguments.strip(),
            raw_input=input_text,
        )

    def dispatch_slash_command(
        self,
        context: SlashCommandContext,
        input_text: str,
    ) -> SlashCommandResult:
        """分发单条输入；非 slash 输入透传给常规 query 路径。

        Args:
            context (SlashCommandContext): 命令执行所需的只读上下文。
            input_text (str): 用户输入文本。

        Returns:
            SlashCommandResult: 分流结果，描述是否已处理以及后续是否继续 query。
        """
        parsed = self.parse_slash_command(input_text)
        if parsed is None:
            return SlashCommandResult(
                handled=False,
                continue_query=True,
                prompt=input_text,
            )

        spec = self.find_slash_command(parsed.command_name)
        if spec is None:
            return self._build_unknown_command_result(parsed.command_name)

        return spec.handler(context, parsed)

    def get_slash_command_specs(self) -> tuple[SlashCommandSpec, ...]:
        """返回当前分发器支持的 slash 命令规格列表。

        Returns:
            tuple[SlashCommandSpec, ...]: 按帮助展示顺序排列的命令规格。
        """
        return self._specs

    def find_slash_command(self, command_name: str) -> SlashCommandSpec | None:
        """按名称查找 slash 命令规格。

        Args:
            command_name (str): 待查找的命令名，可包含大小写与首尾空白。

        Returns:
            SlashCommandSpec | None: 找到时返回规格对象，否则返回 None。
        """
        return self._spec_index.get(command_name.strip().lower())

    def _build_specs(self) -> tuple[SlashCommandSpec, ...]:
        """构建当前分发器支持的全部命令规格。

        Returns:
            tuple[SlashCommandSpec, ...]: 命令规格元组。
        """
        return (
            SlashCommandSpec(names=('help',), description='Show supported local slash commands.', handler=self._handle_help),
            SlashCommandSpec(names=('context',), description='Show local context status.', handler=self._handle_context),
            SlashCommandSpec(names=('status',), description='Show current session status.', handler=self._handle_status),
            SlashCommandSpec(names=('permissions',), description='Show current tool permissions.', handler=self._handle_permissions),
            SlashCommandSpec(names=('tools',), description='List registered local tools.', handler=self._handle_tools),
            SlashCommandSpec(names=('clear',), description='Fork a new cleared session snapshot.', handler=self._handle_clear),
            SlashCommandSpec(names=('exit', 'quit'), description='Stop local interaction and return to caller.', handler=self._handle_exit),
        )

    def _handle_help(
        self,
        context: SlashCommandContext,
        parsed: ParsedSlashCommand,
    ) -> SlashCommandResult:
        """渲染当前支持的本地 slash 命令清单。

        Args:
            context (SlashCommandContext): 命令执行上下文；当前命令不读取其中内容。
            parsed (ParsedSlashCommand): 已解析命令；当前命令不读取其参数。

        Returns:
            SlashCommandResult: 包含帮助文本的本地处理结果。
        """
        del context, parsed
        lines = ['Slash Commands', '==============', '']
        for spec in self.get_slash_command_specs():
            lines.append(f'/{spec.names[0]} - {spec.description}')
        return SlashCommandResult(
            handled=True,
            continue_query=False,
            command_name='help',
            output='\n'.join(lines),
        )

    def _handle_context(
        self,
        context: SlashCommandContext,
        parsed: ParsedSlashCommand,
    ) -> SlashCommandResult:
        """渲染当前会话的上下文预算状态。

        Args:
            context (SlashCommandContext): 命令执行上下文，提供消息、预算与工具注册信息。
            parsed (ParsedSlashCommand): 已解析命令；当前命令不读取其参数。

        Returns:
            SlashCommandResult: 包含上下文预算快照的本地处理结果。
        """
        del parsed
        openai_tools = self._build_openai_tools(context.tool_registry)
        snapshot = self._budget_evaluator.evaluate(
            messages=context.session_state.to_messages(),
            tools=openai_tools,
            max_input_tokens=context.runtime_config.budget_config.max_input_tokens,
        )
        lines = [
            'Context Status',
            '==============',
            f'Messages: {len(context.session_state.messages)}',
            f'Transcript entries: {len(context.session_state.transcript_entries)}',
            f'Tool calls: {context.session_state.tool_call_count}',
            f'Projected input tokens: {snapshot.projected_input_tokens}',
            f'Hard input limit: {self._render_optional_int(snapshot.hard_input_limit)}',
            f'Soft input limit: {self._render_optional_int(snapshot.soft_input_limit)}',
            f'Is soft over: {self._render_bool(snapshot.is_soft_over)}',
            f'Is hard over: {self._render_bool(snapshot.is_hard_over)}',
            f'Compact preserve messages: {context.runtime_config.compact_preserve_messages}',
        ]
        return SlashCommandResult(
            handled=True,
            continue_query=False,
            command_name='context',
            output='\n'.join(lines),
        )

    def _build_openai_tools(self, tool_registry: Mapping[str, AgentTool]) -> list[JSONDict]:
        """把本地工具注册表投影为 OpenAI 工具 schema 列表。

        Args:
            tool_registry (Mapping[str, AgentTool]): 当前会话可见的本地工具注册表。

        Returns:
            list[JSONDict]: 供预算评估器计算 token 占用的工具 schema 列表。
        """
        return [tool.to_openai_tool() for tool in tool_registry.values()]

    def _render_optional_int(self, value: int | None) -> str:
        """把可选整数格式化为展示文本。

        Args:
            value (int | None): 需要展示的整数值；None 表示无限制。

        Returns:
            str: None 返回 unlimited，其余情况返回十进制字符串。
        """
        if value is None:
            return 'unlimited'
        return str(value)

    def _render_bool(self, value: bool) -> str:
        """把布尔值格式化为 yes 或 no。

        Args:
            value (bool): 待格式化的布尔值。

        Returns:
            str: True 返回 yes，False 返回 no。
        """
        return 'yes' if value else 'no'

    def _handle_status(
        self,
        context: SlashCommandContext,
        parsed: ParsedSlashCommand,
    ) -> SlashCommandResult:
        """渲染当前会话的基础状态摘要。

        Args:
            context (SlashCommandContext): 命令执行上下文，提供会话 ID、模型与工作目录信息。
            parsed (ParsedSlashCommand): 已解析命令；当前命令不读取其参数。

        Returns:
            SlashCommandResult: 包含会话状态信息的本地处理结果。
        """
        del parsed
        lines = [
            'Session Status',
            '==============',
            f'Session id: {context.session_id}',
            f'Model: {context.model_config.model}',
            f'Working directory: {context.runtime_config.cwd}',
            f'Completed turns: {context.turns_offset}',
            f'Tool calls: {context.session_state.tool_call_count}',
        ]
        return SlashCommandResult(
            handled=True,
            continue_query=False,
            command_name='status',
            output='\n'.join(lines),
        )

    def _handle_permissions(
        self,
        context: SlashCommandContext,
        parsed: ParsedSlashCommand,
    ) -> SlashCommandResult:
        """渲染当前运行时的权限配置。

        Args:
            context (SlashCommandContext): 命令执行上下文，提供权限配置。
            parsed (ParsedSlashCommand): 已解析命令；当前命令不读取其参数。

        Returns:
            SlashCommandResult: 包含权限摘要的本地处理结果。
        """
        del parsed
        permissions = context.runtime_config.permissions
        lines = [
            'Permissions',
            '===========',
            f'File write: {self._render_bool(permissions.allow_file_write)}',
            f'Shell commands: {self._render_bool(permissions.allow_shell_commands)}',
            f'Destructive shell: {self._render_bool(permissions.allow_destructive_shell_commands)}',
        ]
        return SlashCommandResult(
            handled=True,
            continue_query=False,
            command_name='permissions',
            output='\n'.join(lines),
        )

    def _handle_tools(
        self,
        context: SlashCommandContext,
        parsed: ParsedSlashCommand,
    ) -> SlashCommandResult:
        """渲染当前注册工具与插件摘要。

        Args:
            context (SlashCommandContext): 命令执行上下文，提供工具注册表、权限与插件摘要。
            parsed (ParsedSlashCommand): 已解析命令；当前命令不读取其参数。

        Returns:
            SlashCommandResult: 包含工具列表与插件摘要的本地处理结果。
        """
        del parsed
        permissions = context.runtime_config.permissions
        lines = [
            'Registered Tools',
            '================',
            f'File write enabled: {self._render_bool(permissions.allow_file_write)}',
            f'Shell enabled: {self._render_bool(permissions.allow_shell_commands)}',
            '',
        ]
        for tool in context.tool_registry.values():
            lines.append(f'{tool.name} - {tool.description}')
        if context.plugin_summary.strip():
            lines.extend(['', context.plugin_summary.strip()])
        return SlashCommandResult(
            handled=True,
            continue_query=False,
            command_name='tools',
            output='\n'.join(lines),
        )

    def _handle_clear(
        self,
        context: SlashCommandContext,
        parsed: ParsedSlashCommand,
    ) -> SlashCommandResult:
        """请求上层以 fork 语义切换到一个全新的空会话。

        Args:
            context (SlashCommandContext): 命令执行上下文，提供当前会话历史以判断 had_history。
            parsed (ParsedSlashCommand): 已解析命令；当前命令不读取其参数。

        Returns:
            SlashCommandResult: 包含清空提示与新会话状态的本地处理结果。
        """
        del parsed
        had_history = bool(
            context.session_state.messages
            or context.session_state.transcript_entries
            or context.session_state.tool_call_count
            or context.turns_offset
        )
        return SlashCommandResult(
            handled=True,
            continue_query=False,
            command_name='clear',
            output='Cleared in-memory session context.',
            replacement_session_state=AgentSessionState(),
            fork_session=True,
            metadata={'had_history': had_history},
        )

    def _handle_exit(
        self,
        context: SlashCommandContext,
        parsed: ParsedSlashCommand,
    ) -> SlashCommandResult:
        """处理退出别名命令，通知上层结束本地交互。

        Args:
            context (SlashCommandContext): 命令执行上下文；当前命令不读取其中内容。
            parsed (ParsedSlashCommand): 已解析命令；用于保留用户输入的别名（exit/quit）。

        Returns:
            SlashCommandResult: 包含退出提示的本地处理结果。
        """
        del context
        return SlashCommandResult(
            handled=True,
            continue_query=False,
            command_name=parsed.command_name,
            output='Exiting local session interaction.',
            metadata={'exit_requested': True},
        )

    def _build_unknown_command_result(self, command_name: str) -> SlashCommandResult:
        """为未知 slash 命令构造统一错误结果。

        Args:
            command_name (str): 用户输入的命令名，可能为空字符串。

        Returns:
            SlashCommandResult: 包含 unknown_command 错误码的本地处理结果。
        """
        command_label = command_name or '(empty)'
        return SlashCommandResult(
            handled=True,
            continue_query=False,
            command_name=command_label,
            output=(
                f'Unknown slash command: /{command_label}\n'
                'Run /help to list supported local commands.'
            ),
            metadata={'error': 'unknown_command'},
        )

    def _build_spec_index(
        self,
        specs: tuple[SlashCommandSpec, ...],
    ) -> dict[str, SlashCommandSpec]:
        """根据命令规格构建名称索引。

        Args:
            specs (tuple[SlashCommandSpec, ...]): 按展示顺序排列的命令规格列表。

        Returns:
            dict[str, SlashCommandSpec]: 命令名到规格对象的映射表。
        """
        index: dict[str, SlashCommandSpec] = {}
        for spec in specs:
            for name in spec.names:
                index[name] = spec
        return index

