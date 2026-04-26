"""ISSUE-012 Slash 命令框架与高频命令。"""

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
    """解析后的 slash 命令。"""

    command_name: str
    arguments: str
    raw_input: str


@dataclass(frozen=True)
class SlashCommandContext:
    """Slash 命令执行所需的只读上下文。"""

    session_state: AgentSessionState
    session_id: str
    turns_offset: int
    runtime_config: AgentRuntimeConfig
    model_config: ModelConfig
    tool_registry: Mapping[str, AgentTool]
    plugin_summary: str = ''


@dataclass(frozen=True)
class SlashCommandResult:
    """Slash 命令分流结果。"""

    handled: bool
    continue_query: bool
    command_name: str = ''
    output: str = ''
    prompt: str | None = None
    replacement_session_state: AgentSessionState | None = None
    fork_session: bool = False
    metadata: JSONDict = field(default_factory=dict)


SlashHandler = Callable[[SlashCommandContext, ParsedSlashCommand], SlashCommandResult]


_BUDGET_EVALUATOR = ContextBudgetEvaluator()


@dataclass(frozen=True)
class SlashCommandSpec:
    """单个 slash 命令的规格。"""

    names: tuple[str, ...]
    description: str
    handler: SlashHandler


def parse_slash_command(input_text: str) -> ParsedSlashCommand | None:
    """从原始输入解析 slash 命令。

    Args:
        input_text (str): 用户原始输入。

    Returns:
        ParsedSlashCommand | None: 解析成功返回命令对象，非 slash 输入返回 None。
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
    context: SlashCommandContext,
    input_text: str,
) -> SlashCommandResult:
    """分发单条输入；非 slash 输入透传给常规 query 路径。

    Args:
        context (SlashCommandContext): 命令执行上下文。
        input_text (str): 用户输入文本。

    Returns:
        SlashCommandResult: 分流结果（是否处理、是否继续常规查询等）。
    """
    parsed = parse_slash_command(input_text)
    if parsed is None:
        return SlashCommandResult(
            handled=False,
            continue_query=True,
            prompt=input_text,
        )

    spec = find_slash_command(parsed.command_name)
    if spec is None:
        command_label = parsed.command_name or '(empty)'
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

    return spec.handler(context, parsed)


def get_slash_command_specs() -> tuple[SlashCommandSpec, ...]:
    """返回当前支持的 slash 命令列表。"""
    return (
        SlashCommandSpec(names=('help',), description='Show supported local slash commands.', handler=_handle_help),
        SlashCommandSpec(names=('context',), description='Show local context status.', handler=_handle_context),
        SlashCommandSpec(names=('status',), description='Show current session status.', handler=_handle_status),
        SlashCommandSpec(names=('permissions',), description='Show current tool permissions.', handler=_handle_permissions),
        SlashCommandSpec(names=('tools',), description='List registered local tools.', handler=_handle_tools),
        SlashCommandSpec(names=('clear',), description='Fork a new cleared session snapshot.', handler=_handle_clear),
    )


def find_slash_command(command_name: str) -> SlashCommandSpec | None:
    """按名称查找 slash 命令。"""
    normalized = command_name.strip().lower()
    for spec in get_slash_command_specs():
        if normalized in spec.names:
            return spec
    return None


def _handle_help(context: SlashCommandContext, parsed: ParsedSlashCommand) -> SlashCommandResult:
    """内部方法：执行 `_handle_help` 相关逻辑。
    Args:
        context (SlashCommandContext): 参数 `context`。
        parsed (ParsedSlashCommand): 参数 `parsed`。
    Returns:
        SlashCommandResult: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    lines = ['Slash Commands', '==============', '']
    for spec in get_slash_command_specs():
        lines.append(f'/{spec.names[0]} - {spec.description}')
    return SlashCommandResult(
        handled=True,
        continue_query=False,
        command_name='help',
        output='\n'.join(lines),
    )


def _handle_context(context: SlashCommandContext, parsed: ParsedSlashCommand) -> SlashCommandResult:
    """内部方法：执行 `_handle_context` 相关逻辑。
    Args:
        context (SlashCommandContext): 参数 `context`。
        parsed (ParsedSlashCommand): 参数 `parsed`。
    Returns:
        SlashCommandResult: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    openai_tools = _build_openai_tools(context.tool_registry)
    snapshot = _BUDGET_EVALUATOR.evaluate(
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
        f'Hard input limit: {_render_optional_int(snapshot.hard_input_limit)}',
        f'Soft input limit: {_render_optional_int(snapshot.soft_input_limit)}',
        f'Is soft over: {_render_bool(snapshot.is_soft_over)}',
        f'Is hard over: {_render_bool(snapshot.is_hard_over)}',
        f'Compact preserve messages: {context.runtime_config.compact_preserve_messages}',
    ]
    return SlashCommandResult(
        handled=True,
        continue_query=False,
        command_name='context',
        output='\n'.join(lines),
    )


def _handle_status(context: SlashCommandContext, parsed: ParsedSlashCommand) -> SlashCommandResult:
    """内部方法：执行 `_handle_status` 相关逻辑。
    Args:
        context (SlashCommandContext): 参数 `context`。
        parsed (ParsedSlashCommand): 参数 `parsed`。
    Returns:
        SlashCommandResult: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
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


def _handle_permissions(context: SlashCommandContext, parsed: ParsedSlashCommand) -> SlashCommandResult:
    """内部方法：执行 `_handle_permissions` 相关逻辑。
    Args:
        context (SlashCommandContext): 参数 `context`。
        parsed (ParsedSlashCommand): 参数 `parsed`。
    Returns:
        SlashCommandResult: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    permissions = context.runtime_config.permissions
    lines = [
        'Permissions',
        '===========',
        f'File write: {_render_bool(permissions.allow_file_write)}',
        f'Shell commands: {_render_bool(permissions.allow_shell_commands)}',
        f'Destructive shell: {_render_bool(permissions.allow_destructive_shell_commands)}',
    ]
    return SlashCommandResult(
        handled=True,
        continue_query=False,
        command_name='permissions',
        output='\n'.join(lines),
    )


def _handle_tools(context: SlashCommandContext, parsed: ParsedSlashCommand) -> SlashCommandResult:
    """内部方法：执行 `_handle_tools` 相关逻辑。
    Args:
        context (SlashCommandContext): 参数 `context`。
        parsed (ParsedSlashCommand): 参数 `parsed`。
    Returns:
        SlashCommandResult: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    permissions = context.runtime_config.permissions
    lines = [
        'Registered Tools',
        '================',
        f'File write enabled: {_render_bool(permissions.allow_file_write)}',
        f'Shell enabled: {_render_bool(permissions.allow_shell_commands)}',
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


def _handle_clear(context: SlashCommandContext, parsed: ParsedSlashCommand) -> SlashCommandResult:
    """内部方法：执行 `_handle_clear` 相关逻辑。
    Args:
        context (SlashCommandContext): 参数 `context`。
        parsed (ParsedSlashCommand): 参数 `parsed`。
    Returns:
        SlashCommandResult: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
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


def _build_openai_tools(tool_registry: Mapping[str, AgentTool]) -> list[JSONDict]:
    """内部方法：执行 `_build_openai_tools` 相关逻辑。
    Args:
        tool_registry (Mapping[str, AgentTool]): 参数 `tool_registry`。
    Returns:
        list[JSONDict]: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    return [tool.to_openai_tool() for tool in tool_registry.values()]


def _render_bool(value: bool) -> str:
    """内部方法：执行 `_render_bool` 相关逻辑。
    Args:
        value (bool): 参数 `value`。
    Returns:
        str: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    return 'yes' if value else 'no'


def _render_optional_int(value: int | None) -> str:
    """内部方法：执行 `_render_optional_int` 相关逻辑。
    Args:
        value (int | None): 参数 `value`。
    Returns:
        str: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    if value is None:
        return 'unlimited'
    return str(value)