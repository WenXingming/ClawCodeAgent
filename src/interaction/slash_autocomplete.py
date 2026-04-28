"""slash 自动补全输入适配模块。

本模块把交互循环的输入职责从 CLI 协调器中抽离出来：
1. 在可用时通过 prompt_toolkit 提供 slash 命令补全；
2. 在非 TTY、测试或依赖缺失时回退到内建 input()；
3. 不参与会话推进、命令分发或结果渲染。
"""

from __future__ import annotations

import builtins
import sys
from dataclasses import dataclass
from typing import Callable, TextIO

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.output.color_depth import ColorDepth
    from prompt_toolkit.shortcuts.prompt import CompleteStyle
    from prompt_toolkit.styles import Style
except ImportError:  # pragma: no cover - 依赖缺失时走 input() 回退
    PromptSession = None
    FormattedText = None
    ColorDepth = None
    CompleteStyle = None
    Completer = object
    Completion = None
    Style = None


@dataclass(frozen=True)
class SlashAutocompleteEntry:
    """描述一个可补全的 slash 命令项。"""

    name: str
    description: str


class SlashAutocompleteCatalog:
    """维护 slash 命令补全项并根据当前输入返回候选。"""

    def __init__(self, entries: tuple[SlashAutocompleteEntry, ...]) -> None:
        """初始化补全目录。"""
        self._entries = entries

    def get_matches(self, input_text: str) -> tuple[SlashAutocompleteEntry, ...]:
        """根据当前输入返回可补全的 slash 命令候选。"""
        stripped = input_text.lstrip()
        if not stripped.startswith('/'):
            return ()

        body = stripped[1:]
        if any(char.isspace() for char in body):
            return ()

        prefix = body.strip().lower()
        return tuple(entry for entry in self._entries if entry.name.startswith(prefix))


class _PromptToolkitSlashAutocompleteCompleter(Completer):
    """把 slash 补全目录适配为 prompt_toolkit completer。"""

    def __init__(self, catalog: SlashAutocompleteCatalog) -> None:
        self._catalog = catalog

    def get_completions(self, document, complete_event):  # type: ignore[override]
        del complete_event
        text_before_cursor = document.text_before_cursor
        stripped = text_before_cursor.lstrip()
        if not stripped.startswith('/'):
            return

        body = stripped[1:]
        if any(char.isspace() for char in body):
            return

        prefix = body.strip().lower()
        for entry in self._catalog.get_matches(text_before_cursor):
            yield Completion(
                entry.name,
                start_position=-len(prefix),
                display=f'/{entry.name}',
                display_meta=entry.description,
                style='class:slash-autocomplete.command',
                selected_style='class:slash-autocomplete.command.current',
            )


class SlashAutocompletePrompt:
    """提供支持 slash 自动补全的交互式输入读取器。"""

    _PLACEHOLDER_TEXT = 'Type / to browse local slash commands'
    _PROMPT_STYLE_RULES = {
        'prompt.label': 'bold #e6edf7',
        'prompt.chevron': 'bold #79d9ea',
        'placeholder': '#5f7087',
        'completion-menu': 'bg:default #d7e1ee',
        'completion-menu.completion': 'bg:default #d7e1ee',
        'completion-menu.completion.current': 'noreverse bg:#18222d #eef6ff',
        'completion-menu.meta.completion': 'bg:default #8597ad',
        'completion-menu.meta.completion.current': 'noreverse bg:#18222d #a8bacf',
        'completion-menu.multi-column-meta': 'bg:default #8597ad',
        'completion-toolbar': 'bg:default #d7e1ee',
        'completion-toolbar.completion': 'bg:default #d7e1ee',
        'completion-toolbar.completion.current': 'noreverse bg:#18222d #eef6ff',
        'completion-toolbar.arrow': 'bg:default #46586d',
        'scrollbar.background': 'bg:default',
        'scrollbar.button': 'bg:default',
        'scrollbar.arrow': 'bg:default #223040',
        'slash-autocomplete.command': '#74e2ee',
        'slash-autocomplete.command.current': 'bold #b9f7ff',
    }

    def __init__(
        self,
        *,
        entries: tuple[SlashAutocompleteEntry, ...],
        fallback_reader: Callable[[str], str] | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        """初始化输入读取器。"""
        self._fallback_reader = fallback_reader or builtins.input
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._catalog = SlashAutocompleteCatalog(entries)
        self._session = self._build_prompt_session()

    def read(self, prompt_text: str) -> str:
        """读取一轮用户输入。"""
        if self._session is None:
            return self._fallback_reader(prompt_text)
        return self._session.prompt(self._format_prompt_message(prompt_text))

    def _build_prompt_session(self):
        """在环境支持时创建 prompt_toolkit session。"""
        if PromptSession is None:
            return None
        if not self._supports_interactive_prompt():
            return None
        return PromptSession(
            completer=_PromptToolkitSlashAutocompleteCompleter(self._catalog),
            complete_while_typing=True,
            reserve_space_for_menu=8,
            complete_style=CompleteStyle.COLUMN,
            style=self._build_prompt_style(),
            include_default_pygments_style=False,
            color_depth=ColorDepth.TRUE_COLOR,
            placeholder=self._build_placeholder(),
        )

    def _build_prompt_style(self):
        """构建补全菜单与提示符样式。"""
        if Style is None:
            return None
        return Style.from_dict(self._PROMPT_STYLE_RULES)

    def _build_placeholder(self):
        """构建空输入状态下的提示文本。"""
        if FormattedText is None:
            return self._PLACEHOLDER_TEXT
        return FormattedText([
            ('class:placeholder', self._PLACEHOLDER_TEXT),
        ])

    def _format_prompt_message(self, prompt_text: str):
        """为 prompt_toolkit 构建更清晰的提示符样式。"""
        if FormattedText is None:
            return prompt_text

        normalized_prompt = prompt_text.rstrip()
        if normalized_prompt.endswith('>'):
            prompt_label = normalized_prompt[:-1].rstrip()
            return FormattedText([
                ('class:prompt.label', prompt_label),
                ('class:prompt.chevron', '> '),
            ])
        return FormattedText([
            ('class:prompt.label', prompt_text),
        ])

    def _supports_interactive_prompt(self) -> bool:
        """仅在真实交互式终端启用 prompt_toolkit。"""
        input_is_tty = getattr(self._stdin, 'isatty', None)
        output_is_tty = getattr(self._stdout, 'isatty', None)
        return bool(callable(input_is_tty) and input_is_tty() and callable(output_is_tty) and output_is_tty())