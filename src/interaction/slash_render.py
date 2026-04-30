"""Slash 命令终端渲染模块。

本模块只负责把 slash 命令的纯文本语义结果渲染成统一的信息面板，
不参与命令解析、命令匹配、会话状态推进或结果持久化。
"""

from __future__ import annotations

import shutil
from typing import Mapping, TextIO

from .terminal_render import TerminalRenderer


class SlashCommandRenderer(TerminalRenderer):
    """把 slash 命令结果渲染为统一的终端面板。

    外部通过 render() 传入命令名与输出文本；本类自动解析标题、宽度自适应换行
    并把内容输入边框渲染器。
    """

    _DEFAULT_TITLES = {
        'help': 'Slash Commands',
        'context': 'Context Status',
        'status': 'Session Status',
        'permissions': 'Permissions',
        'tools': 'Registered Tools',
        'clear': 'Session Cleared',
        'exit': 'Interaction Closed',
        'quit': 'Interaction Closed',
    }
    _COMPACT_COMMANDS = frozenset({'clear', 'exit', 'quit'})

    def __init__(self, *, top_padding: int = 0, bottom_padding: int = 0) -> None:
        """初始化 slash 渲染器。

        Args:
            top_padding (int): 面板上方外边距行数，最小值为 0。
            bottom_padding (int): 面板下方外边距行数，最小值为 0。
        Returns:
            None: 构造函数只建立渲染器内部状态。
        Raises:
            无。
        """
        super().__init__(
            frame_horizontal_padding=1,
            frame_vertical_padding=0,
            top_padding=top_padding,
            bottom_padding=bottom_padding,
        )

    def render(
        self,
        *,
        command_name: str,
        output: str,
        metadata: Mapping[str, object] | None = None,
        stream: TextIO | None = None,
    ) -> None:
        """渲染单次 slash 命令结果。

        Args:
            command_name (str): 命令名，用于决定标题和布局风格。
            output (str): 命令返回的纯文本信息。
            metadata (Mapping[str, object] | None): 可选的元数据，包含 error 等字段。
            stream (TextIO | None): 目标输出流；None 时默认使用 sys.stdout。
        Returns:
            None: 该方法只负责把面板内容写入目标流。
        Raises:
            无。
        """
        normalized_command = command_name.strip().lower()
        effective_metadata = dict(metadata or {})
        title = self._resolve_title(normalized_command, effective_metadata)
        body_lines = self._normalize_body_lines(output)
        content_lines = self._build_content_lines(title, body_lines, normalized_command, effective_metadata)
        wrapped_lines = self._wrap_content_lines(content_lines, stream=stream)
        self._render_block(wrapped_lines, stream=stream, active_title=title)

    def _resolve_title(self, command_name: str, metadata: Mapping[str, object]) -> str:
        """根据命令名与错误元数据决定面板标题。

        Args:
            command_name (str): 解析后的命令名（小写）。
            metadata (Mapping[str, object]): 包含 error 字段的元数据字典。
        Returns:
            str: 面板顶部标题文本。
        Raises:
            无。
        """
        error_code = metadata.get('error')
        if error_code == 'ambiguous_command':
            return 'Slash Command Matches'
        if error_code == 'unknown_command':
            return 'Unknown Slash Command'
        return self._DEFAULT_TITLES.get(command_name, 'Slash Command')

    def _build_content_lines(
        self,
        title: str,
        body_lines: tuple[str, ...],
        command_name: str,
        metadata: Mapping[str, object],
    ) -> tuple[str, ...]:
        """组装面板标题与正文。

        Args:
            title (str): 面板标题文本。
            body_lines (tuple[str, ...]): 已清理后的正文行列表。
            command_name (str): 命令名，用于判断是否使用紧凑布局。
            metadata (Mapping[str, object]): 命令元数据，用于判断错误状态。
        Returns:
            tuple[str, ...]: 含标题与阐隔空行的完整内容行元组。
        Raises:
            无。
        """
        if self._should_use_compact_layout(command_name, metadata):
            return (title, *body_lines)
        return (title, '', *body_lines)

    def _wrap_content_lines(self, content_lines: tuple[str, ...], *, stream: TextIO | None) -> tuple[str, ...]:
        """按当前终端宽度对 slash 面板内容执行软换行。

        Args:
            content_lines (tuple[str, ...]): 原始内容行。
            stream (TextIO | None): 目标输出流；目前实现未使用该参数。
        Returns:
            tuple[str, ...]: 按终端宽度折行后的内容行元组。
        Raises:
            无。
        """
        max_content_width = self._resolve_max_content_width(stream)
        if max_content_width <= 0:
            return content_lines

        wrapped_lines: list[str] = []
        for line in content_lines:
            wrapped_lines.extend(self._wrap_line_to_width(line, max_content_width))
        return tuple(wrapped_lines)

    def _resolve_max_content_width(self, stream: TextIO | None) -> int:
        """根据当前终端列宽计算框体正文允许的最大宽度。

        Args:
            stream (TextIO | None): 目标输出流（暂未使用，保留未来扩展）。
        Returns:
            int: 正文区域允许的最大宽度（字符数），最小为 20。
        Raises:
            无。
        """
        del stream
        terminal_columns = shutil.get_terminal_size(fallback=(120, 24)).columns
        frame_overhead = self._frame_horizontal_padding * 2 + 2
        return max(terminal_columns - frame_overhead, 20)

    def _wrap_line_to_width(self, text: str, max_width: int) -> tuple[str, ...]:
        """把单行文本按显示宽度折为多行。

        Args:
            text (str): 待换行的原始单行文本。
            max_width (int): 每行允许的最大显示宽度（字符数）。
        Returns:
            tuple[str, ...]: 换行后的多行元组；无需换行时返回单元组。
        Raises:
            无。
        """
        if not text or self._display_width(text) <= max_width:
            return (text,)

        wrapped_lines: list[str] = []
        remaining = text
        continuation_prefix = self._build_continuation_prefix(text)
        is_first_segment = True

        while remaining:
            prefix = '' if is_first_segment else continuation_prefix
            available_width = max(max_width - self._display_width(prefix), 1)
            segment, remaining = self._split_wrapped_segment(remaining, available_width)
            wrapped_lines.append(f'{prefix}{segment}')
            is_first_segment = False

        return tuple(wrapped_lines)

    def _build_continuation_prefix(self, text: str) -> str:
        """为形如 `name - description` 的行构造续行悬挂缩进。

        Args:
            text (str): 待换行的原始单行文本。
        Returns:
            str: 续行时使用的头部空格字符串。
        Raises:
            无。
        """
        leading_spaces = len(text) - len(text.lstrip(' '))
        stripped = text.lstrip(' ')
        if ' - ' not in stripped:
            return ' ' * leading_spaces

        label, _, _ = stripped.partition(' - ')
        indent_width = leading_spaces + self._display_width(f'{label} - ')
        return ' ' * indent_width

    def _split_wrapped_segment(self, text: str, max_width: int) -> tuple[str, str]:
        """从单行文本中切出一个不超过目标宽度的片段。

        Args:
            text (str): 待分割的文本，不为空。
            max_width (int): 首段允许的最大显示宽度。
        Returns:
            tuple[str, str]: (head, tail)，head 为切出的首段，tail 为剩余内容；全部切出时 tail 为空。
        Raises:
            无。
        """
        if self._display_width(text) <= max_width:
            return text, ''

        current_width = 0
        last_break_index = -1
        for index, char in enumerate(text):
            char_width = self._character_display_width(char)
            if current_width + char_width > max_width:
                split_index = last_break_index if last_break_index > 0 else index
                head = text[:split_index].rstrip()
                tail = text[split_index:].lstrip()
                if not head:
                    head = text[: max(index, 1)]
                    tail = text[max(index, 1):].lstrip()
                return head, tail

            current_width += char_width
            if char.isspace():
                last_break_index = index + 1

        return text, ''

    @staticmethod
    def _should_use_compact_layout(command_name: str, metadata: Mapping[str, object]) -> bool:
        """为状态型命令使用更紧凑的布局。

        Args:
            command_name (str): 终止类命令名（如 exit/quit/clear）。
            metadata (Mapping[str, object]): 包含 error 字段的元数据字典。
        Returns:
            bool: 需要紧凑布局时返回 True；错误或常规命令返回 False。
        Raises:
            无。
        """
        if metadata.get('error'):
            return False
        return command_name in SlashCommandRenderer._COMPACT_COMMANDS

    @staticmethod
    def _normalize_body_lines(output: str) -> tuple[str, ...]:
        """移除旧式标题行，只保留正文内容。

        Args:
            output (str): slash 命令返回的原始输出文本。
        Returns:
            tuple[str, ...]: 移除旧式标题和分隔线后的正文行元组。
        Raises:
            无。
        """
        raw_lines = tuple(output.splitlines()) or ('',)
        if len(raw_lines) >= 2 and raw_lines[0].strip() and set(raw_lines[1].strip()) == {'='}:
            trimmed_lines = raw_lines[2:]
            if trimmed_lines and trimmed_lines[0] == '':
                trimmed_lines = trimmed_lines[1:]
            return trimmed_lines or ('',)
        return raw_lines

    def _render_content_text(
        self,
        text: str,
        content_width: int,
        use_ansi: bool,
        *,
        active_title: str = '',
    ) -> str:
        """对标题行应用渐变强调色，其余正文保持原样。

        Args:
            text (str): 当前正文行。
            content_width (int): 正文区域的目标宽度。
            use_ansi (bool): 是否启用 ANSI 着色。
            active_title (str): 需要渐变色高亮的标题文本；空字符串表示无标题高亮。
        Returns:
            str: 已补齐宽度的单行文本；标题行包含渐变着色。
        Raises:
            无。
        """
        padded_text = self._pad_to_display_width(text, content_width)
        if use_ansi and active_title and text == active_title:
            return self._colorize_gradient_line(padded_text)
        return padded_text

    def _colorize_gradient_line(self, text: str) -> str:
        """为一整行文本应用渐变色。

        Args:
            text (str): 待着色的单行文本（可能含尾部补空格）。
        Returns:
            str: 包含每个非空格字符独立 ANSI 著色的单行文本。
        Raises:
            无。
        """
        visible_positions = [index for index, char in enumerate(text) if char != ' ']
        if not visible_positions:
            return text

        total = max(len(visible_positions) - 1, 1)
        rendered: list[str] = []
        visible_index = 0
        for char in text:
            if char == ' ':
                rendered.append(char)
                continue
            red, green, blue = self._interpolate_gradient(visible_index / total)
            rendered.append(f'\x1b[38;2;{red};{green};{blue}m{char}')
            visible_index += 1
        rendered.append('\x1b[0m')
        return ''.join(rendered)