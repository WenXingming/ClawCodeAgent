"""interaction 包公开入口。

外部代码（生产路径）应仅使用 InteractionGateway。
以下内部类的导出仅为单元测试提供隔离测试能力，生产代码不应直接依赖它们。
"""

from .interaction_gateway import InteractionGateway

# ── 测试兼容导出（仅供 test/interaction/ 单元测试直接实例化子组件使用） ──
from .quit_render import ExitRenderer
from .runtime_event_printer import RuntimeEventPrinter
from .session_summary import SessionInteractionTracker
from .slash_autocomplete import SlashAutocompleteCatalog, SlashAutocompletePrompt
from .slash_commands import SlashCommandDispatcher
from .slash_render import SlashCommandRenderer
from .startup_render import StartupRenderer

__all__ = [
    # 主公开入口
    'InteractionGateway',
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
