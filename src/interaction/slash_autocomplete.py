"""slash 自动补全输入适配模块。

本模块把交互循环的输入职责从 CLI 协调器中抽离出来：
1. 在可用时通过 prompt_toolkit 提供 slash 命令补全；
2. 在非 TTY、测试或依赖缺失时回退到内建 input()；
3. 不参与会话推进、命令分发或结果渲染。
"""

from __future__ import annotations

import builtins
import sys
from typing import Callable, TextIO

from core_contracts.interaction import SlashAutocompleteEntry

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


class SlashAutocompleteCatalog:
    """维护 slash 命令补全项并根据当前输入返回候选。

    工作流：构造时接收全部补全项目录；get_matches() 根据当前输入的前缀
    过滤出可供候选的 slash 命令列表。
    """

    def __init__(self, entries: tuple[SlashAutocompleteEntry, ...]) -> None:
        """初始化补全目录。

        Args:
            entries (tuple[SlashAutocompleteEntry, ...]): 全部已知的补全项列表。
        Returns:
            None: 构造函数只存储补全项。
        Raises:
            无。
        """
        self._entries = entries  # tuple[SlashAutocompleteEntry, ...]: 全部可补全的命令条目。

    def get_matches(self, input_text: str) -> tuple[SlashAutocompleteEntry, ...]:
        """根据当前输入返回可补全的 slash 命令候选。

        Args:
            input_text (str): 用户已输入的文本；仅在以 / 开头且无空格时触发补全。
        Returns:
            tuple[SlashAutocompleteEntry, ...]: 与当前前缀匹配的补全项列表；无匹配时返回空元组。
        Raises:
            无。
        """
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
        """初始化 prompt_toolkit 补全适配器。

        Args:
            catalog (SlashAutocompleteCatalog): 提供匹配逻辑的补全目录。
        Returns:
            None: 构造函数只存储目录引用。
        Raises:
            无。
        """
        self._catalog = catalog  # SlashAutocompleteCatalog: 提供命令前缀匹配的补全目录。

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
        """初始化输入读取器。

        Args:
            entries (tuple[SlashAutocompleteEntry, ...]): 全部可补全的命令条目列表。
            fallback_reader (Callable[[str], str] | None): 不支持 prompt_toolkit 时使用的输入函数；None 时使用 builtins.input。
            stdin (TextIO | None): 标准输入流；None 时使用 sys.stdin。
            stdout (TextIO | None): 标准输出流；None 时使用 sys.stdout。
        Returns:
            None: 构造函数只建立读取器内部状态。
        Raises:
            无。
        """
        self._fallback_reader = fallback_reader or builtins.input
        # Callable[[str], str]: 非 TTY 或 prompt_toolkit 不可用时的回退输入函数。
        self._stdin = stdin or sys.stdin
        # TextIO: 用于 isatty() 检测的标准输入流。
        self._stdout = stdout or sys.stdout
        # TextIO: 用于 isatty() 检测的标准输出流。
        self._catalog = SlashAutocompleteCatalog(entries)
        # SlashAutocompleteCatalog: 当前可用的补全目录，供 completer 查询。
        self._session = self._build_prompt_session()
        # PromptSession | None: prompt_toolkit 会话实例；环境不支持时为 None。

    def read(self, prompt_text: str) -> str:
        """读取一轮用户输入。

        Args:
            prompt_text (str): 提示符文本，显示在输入框前。
        Returns:
            str: 用户输入的原始文本。
        Raises:
            KeyboardInterrupt: 用户按下 Ctrl+C 时透传给调用方。
            EOFError: 输入流关闭时透传给调用方。
        """
        if self._session is None:
            return self._fallback_reader(prompt_text)
        return self._session.prompt(self._format_prompt_message(prompt_text))

    def _build_prompt_session(self):
        """在环境支持时创建 prompt_toolkit 会话。

        Returns:
            PromptSession | None: 支持时返回已配置的 prompt_toolkit 会话；否则返回 None。
        Raises:
            无。
        """
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
        """构建补全菜单与提示符样式。

        Returns:
            Style | None: 支持 prompt_toolkit 时返回样式对象；否则返回 None。
        Raises:
            无。
        """
        if Style is None:
            return None
        return Style.from_dict(self._PROMPT_STYLE_RULES)

    def _build_placeholder(self):
        """构建空输入状态下的灰色占位提示文本。

        Returns:
            FormattedText | str: prompt_toolkit 可用时返回 FormattedText；否则返回纯字符串。
        Raises:
            无。
        """
        if FormattedText is None:
            return self._PLACEHOLDER_TEXT
        return FormattedText([
            ('class:placeholder', self._PLACEHOLDER_TEXT),
        ])

    def _format_prompt_message(self, prompt_text: str):
        """将提示符文本格式化为 prompt_toolkit 支持的样式化消息。

        Args:
            prompt_text (str): 原始提示符字符串。
        Returns:
            FormattedText | str: prompt_toolkit 可用时返回已样式化文本；否则返回原始字符串。
        Raises:
            无。
        """
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
        """检测当前环境是否支持交互式 prompt_toolkit 输入。

        Returns:
            bool: 输入流与输出流均为 TTY 时返回 True，否则返回 False。
        Raises:
            无。
        """
        input_is_tty = getattr(self._stdin, 'isatty', None)
        output_is_tty = getattr(self._stdout, 'isatty', None)
        return bool(callable(input_is_tty) and input_is_tty() and callable(output_is_tty) and output_is_tty())


__all__ = ['SlashAutocompleteEntry', 'SlashAutocompletePrompt']