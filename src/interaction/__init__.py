"""interaction 包公开入口。

架构约定：
- 对外仅暴露 InteractionGateway 与 create_interaction_gateway 工厂函数。
- 所有外部消费者必须通过本入口访问交互能力；禁止跨包直接依赖内部子模块。
- 内部实现类型（Snipper / Compactor 等）仅允许通过 interaction 包路径访问
  （用于单元测试白盒场景）。
"""

from __future__ import annotations

import sys
from typing import TextIO

from context.context_gateway import ContextGateway
from core_contracts.interaction_contracts import SlashAutocompleteEntry, SlashCommandSpec

from .interaction_gateway import InteractionGateway
from .quit_render import ExitRenderer
from .runtime_event_printer import RuntimeEventPrinter
from .session_summary import SessionInteractionTracker
from .slash_autocomplete import SlashAutocompleteCatalog, SlashAutocompletePrompt
from .slash_commands import SlashCommandDispatcher
from .slash_render import SlashCommandRenderer
from .startup_render import StartupRenderer


def create_interaction_gateway(
    context_gateway: ContextGateway | None = None,
    *,
    stream: TextIO | None = None,
    stdin: TextIO | None = None,
    startup_lines: tuple[str, ...] | None = None,
    startup_subtitle: str | None = None,
    exit_title: str = 'Agent powering down. Goodbye!',
) -> InteractionGateway:
    """工厂函数：构造全部内部组件并通过依赖注入装配 InteractionGateway。

    调用方只需传入可选的上下文网关与 I/O 流；SlashCommandDispatcher、
    StartupRenderer、ExitRenderer 等所有内部组件的实例化由本工厂统一负责，
    外部无需感知任何内部构件。

    Args:
        context_gateway (ContextGateway | None): 可选上下文网关；为 None 时
            /context 命令降级返回提示信息。
        stream (TextIO | None): 统一输出流；None 时默认 sys.stdout。
        stdin (TextIO | None): 统一输入流；None 时默认 sys.stdin。
        startup_lines (tuple[str, ...] | None): 自定义 ASCII-art 标题行。
        startup_subtitle (str | None): 自定义副标题文本。
        exit_title (str): 退出提示框标题文本。
    Returns:
        InteractionGateway: 完整初始化的交互网关实例。
    Raises:
        无。
    """
    _stream = stream or sys.stdout
    _stdin = stdin or sys.stdin

    dispatcher = SlashCommandDispatcher(context_manager=context_gateway)
    _build_and_load_specs(dispatcher)

    startup_renderer = StartupRenderer(lines=startup_lines, subtitle=startup_subtitle)
    exit_renderer = ExitRenderer(title=exit_title)
    slash_renderer = SlashCommandRenderer()
    event_printer = RuntimeEventPrinter(stream=_stream)

    autocomplete_entries = _build_autocomplete_entries(dispatcher)
    autocomplete_prompt = SlashAutocompletePrompt(
        entries=autocomplete_entries,
        stdin=_stdin,
        stdout=_stream,
    )

    return InteractionGateway(
        context_gateway=context_gateway,
        dispatcher=dispatcher,
        startup_renderer=startup_renderer,
        exit_renderer=exit_renderer,
        slash_renderer=slash_renderer,
        event_printer=event_printer,
        autocomplete_prompt=autocomplete_prompt,
        stream=_stream,
        stdin=_stdin,
    )


def _build_and_load_specs(dispatcher: SlashCommandDispatcher) -> None:
    """为分发器装配所有内置 slash 命令规格。

    命令注册逻辑在此集中管理，符合开闭原则：新增命令仅需修改本函数，
    无需修改 SlashCommandDispatcher 的核心代码。

    Args:
        dispatcher (SlashCommandDispatcher): 待装配命令规格的分发器实例。
    Returns:
        None: 通过 dispatcher.load_specs() 就地装载命令列表。
    """
    specs: tuple[SlashCommandSpec, ...] = (
        SlashCommandSpec(
            names=('help',),
            description='Show supported local slash commands.',
            handler=dispatcher._handle_help,
        ),
        SlashCommandSpec(
            names=('context',),
            description='Show local context status.',
            handler=dispatcher._handle_context,
        ),
        SlashCommandSpec(
            names=('status',),
            description='Show current session status.',
            handler=dispatcher._handle_status,
        ),
        SlashCommandSpec(
            names=('permissions',),
            description='Show current tool permissions.',
            handler=dispatcher._handle_permissions,
        ),
        SlashCommandSpec(
            names=('tools',),
            description='List registered local tools.',
            handler=dispatcher._handle_tools,
        ),
        SlashCommandSpec(
            names=('clear',),
            description='Fork a new cleared session snapshot.',
            handler=dispatcher._handle_clear,
        ),
        SlashCommandSpec(
            names=('exit', 'quit'),
            description='Stop local interaction and return to caller.',
            handler=dispatcher._handle_exit,
        ),
    )
    dispatcher.load_specs(specs)


def _build_autocomplete_entries(
    dispatcher: SlashCommandDispatcher,
) -> tuple[SlashAutocompleteEntry, ...]:
    """从分发器命令规格构建自动补全条目（展开所有别名）。

    Args:
        dispatcher (SlashCommandDispatcher): 已装配规格的 slash 分发器。
    Returns:
        tuple[SlashAutocompleteEntry, ...]: 每个命令名展开为独立补全条目。
    """
    return tuple(
        SlashAutocompleteEntry(name=name, description=spec.description)
        for spec in dispatcher.get_slash_command_specs()
        for name in spec.names
    )


__all__ = [
    'InteractionGateway',
    'create_interaction_gateway',
    # 测试兼容导出
    'ExitRenderer',
    'RuntimeEventPrinter',
    'SessionInteractionTracker',
    'SlashAutocompleteCatalog',
    'SlashAutocompletePrompt',
    'SlashCommandDispatcher',
    'SlashCommandRenderer',
    'StartupRenderer',
]
