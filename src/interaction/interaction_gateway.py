"""interaction 域唯一对外网关。

所有跨域访问必须通过本文件，禁止直接 import interaction 包或其子模块。
"""

from core_contracts.interaction_contracts import (
    EnvironmentLoadSummary,
    SessionSummary,
    SlashAutocompleteEntry,
    SlashCommandContext,
    SlashCommandResult,
)

from .quit_render import ExitRenderer
from .runtime_event_printer import RuntimeEventPrinter
from .session_summary import SessionInteractionTracker
from .slash_autocomplete import SlashAutocompleteCatalog, SlashAutocompletePrompt
from .slash_commands import (
    ParsedSlashCommand,
    SlashCommandDispatcher,
    SlashCommandResolution,
    SlashCommandSpec,
)
from .slash_render import SlashCommandRenderer
from .startup_render import StartupRenderer
from .terminal_render import TerminalRenderer


class InteractionGateway:
    """interaction 领域网关。

    本类只承担“稳定入口”职责：
    1. 对外公开可注入的控制面组件类型；
    2. 暴露跨域共享契约类型；
    3. 屏蔽 interaction 子模块文件布局。
    """

    StartupRenderer = StartupRenderer
    ExitRenderer = ExitRenderer
    RuntimeEventPrinter = RuntimeEventPrinter
    SlashCommandRenderer = SlashCommandRenderer
    SlashCommandDispatcher = SlashCommandDispatcher
    SlashAutocompletePrompt = SlashAutocompletePrompt
    SlashAutocompleteCatalog = SlashAutocompleteCatalog
    SessionInteractionTracker = SessionInteractionTracker
    TerminalRenderer = TerminalRenderer

    EnvironmentLoadSummary = EnvironmentLoadSummary
    SessionSummary = SessionSummary
    SlashAutocompleteEntry = SlashAutocompleteEntry
    SlashCommandContext = SlashCommandContext
    SlashCommandResult = SlashCommandResult
    ParsedSlashCommand = ParsedSlashCommand
    SlashCommandSpec = SlashCommandSpec
    SlashCommandResolution = SlashCommandResolution

__all__ = [
    'InteractionGateway',
    'EnvironmentLoadSummary',
    'ExitRenderer',
    'RuntimeEventPrinter',
    'SessionSummary',
    'SessionInteractionTracker',
    'ParsedSlashCommand',
    'SlashAutocompleteEntry',
    'SlashAutocompleteCatalog',
    'SlashAutocompletePrompt',
    'SlashCommandContext',
    'SlashCommandDispatcher',
    'SlashCommandResolution',
    'SlashCommandResult',
    'SlashCommandSpec',
    'SlashCommandRenderer',
    'StartupRenderer',
    'TerminalRenderer',
]
