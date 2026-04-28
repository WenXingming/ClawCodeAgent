"""终端渲染基础模块。

本模块负责收敛交互层不同终端组件共享的渲染基础能力，当前主要包括：
1. ANSI 能力检测；
2. 带圆角边框的终端块渲染；
3. 空行输出、单行输出与渐变颜色插值。

类名保持宽泛，是为了给未来非框体终端渲染留出演进空间；当前版本的核心能力
仍然以带边框的终端块渲染为主。
"""

from __future__ import annotations

import os
import sys
from typing import TextIO


class TerminalRenderer:
    """为交互层终端输出提供共享的渲染基础设施。

    该基类当前主要负责把若干正文行渲染成带边框的终端块。子类负责准备正文内容、
    决定是否追加额外区域，并按需覆盖边框或正文的着色策略。
    """

    _SOFT_WHITE_RGB = (0xE4, 0xE8, 0xF0)  # tuple[int, int, int]: 默认边框使用的柔和白色 RGB。
    _GRADIENT_STOPS = (
        (0x4B, 0x7B, 0xFF),
        (0x22, 0xC5, 0xC7),
        (0xF4, 0x5D, 0x8D),
    )  # tuple[tuple[int, int, int], ...]: 默认渐变颜色锚点序列。

    def __init__(
        self,
        *,
        frame_horizontal_padding: int,
        frame_vertical_padding: int,
        top_padding: int,
        bottom_padding: int,
    ) -> None:
        """初始化共享终端渲染状态。

        Args:
            frame_horizontal_padding (int): 边框左右内边距，单位为字符。
            frame_vertical_padding (int): 边框上下内边距，单位为行数。
            top_padding (int): 整块内容上方外边距行数。
            bottom_padding (int): 整块内容下方外边距行数。
        Returns:
            None: 构造函数只建立渲染基础状态。
        """
        self._frame_horizontal_padding = max(frame_horizontal_padding, 0)
        # int: 边框左右内边距，单位为字符。
        self._frame_vertical_padding = max(frame_vertical_padding, 0)
        # int: 边框上下内边距，单位为字符行数。
        self._top_padding = max(top_padding, 0)
        # int: 整块内容上方外边距行数。
        self._bottom_padding = max(bottom_padding, 0)
        # int: 整块内容下方外边距行数。

    def _render_block(self, content_lines: tuple[str, ...], stream: TextIO | None = None) -> None:
        """把给定正文行渲染为一整块终端输出。

        Args:
            content_lines (tuple[str, ...]): 需要被渲染的正文行元组。
            stream (TextIO | None): 目标文本流；为 None 时默认使用 sys.stdout。
        Returns:
            None: 该方法只负责把终端块写入目标流。
        """
        target = stream or sys.stdout
        use_ansi = self._stream_supports_ansi(target)

        self._write_blank_lines(target, self._top_padding)
        self._render_frame(target, content_lines, use_ansi=use_ansi)
        self._write_blank_lines(target, self._bottom_padding)

    def _render_frame(
        self,
        stream: TextIO,
        content_lines: tuple[str, ...],
        *,
        use_ansi: bool,
    ) -> None:
        """把正文行渲染为带边框的单块内容。

        Args:
            stream (TextIO): 目标文本流。
            content_lines (tuple[str, ...]): 需要被框起来的正文行元组。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            None: 该方法只负责输出带边框的内容行。
        """
        framed_lines = self._build_framed_lines(content_lines, use_ansi)
        for line in framed_lines:
            self._write_line(stream, line)

    def _stream_supports_ansi(self, stream: TextIO) -> bool:
        """检测目标流是否支持 ANSI 转义序列。

        Args:
            stream (TextIO): 待检测的目标文本流。
        Returns:
            bool: 支持 ANSI 时返回 True，否则返回 False。
        """
        if os.getenv('NO_COLOR'):
            return False

        is_tty = getattr(stream, 'isatty', None)
        if callable(is_tty) and not is_tty():
            return False

        if os.name != 'nt':
            return True

        return any(
            os.getenv(key)
            for key in ('WT_SESSION', 'ANSICON', 'ConEmuANSI', 'TERM_PROGRAM', 'TERM')
        )

    def _build_framed_lines(
        self,
        content_lines: tuple[str, ...],
        use_ansi: bool,
    ) -> tuple[str, ...]:
        """构建带圆角边框的完整输出行。

        Args:
            content_lines (tuple[str, ...]): 原始正文行。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            tuple[str, ...]: 已添加边框与内边距的完整文本行元组。
        """
        padded_lines = self._apply_vertical_padding(content_lines)
        content_width = max((len(line) for line in padded_lines), default=0)
        inner_width = content_width + self._frame_horizontal_padding * 2
        rendered_lines = [self._frame_border_line(inner_width, top=True, use_ansi=use_ansi)]
        rendered_lines.extend(
            self._frame_content_line(line, content_width, use_ansi)
            for line in padded_lines
        )
        rendered_lines.append(self._frame_border_line(inner_width, top=False, use_ansi=use_ansi))
        return tuple(rendered_lines)

    def _apply_vertical_padding(self, content_lines: tuple[str, ...]) -> tuple[str, ...]:
        """在正文上下补齐边框内边距所需的空白行。

        Args:
            content_lines (tuple[str, ...]): 原始正文行。
        Returns:
            tuple[str, ...]: 追加上下内边距后的正文行元组。
        """
        vertical_padding = ('',) * self._frame_vertical_padding
        return (*vertical_padding, *content_lines, *vertical_padding)

    def _frame_border_line(self, inner_width: int, *, top: bool, use_ansi: bool) -> str:
        """构建顶部或底部边框行。

        Args:
            inner_width (int): 边框内部宽度，不含左右角字符。
            top (bool): True 生成顶部边框，False 生成底部边框。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 已按需要着色的单行边框文本。
        """
        left_corner, right_corner = ('╭', '╮') if top else ('╰', '╯')
        border = f'{left_corner}{"─" * inner_width}{right_corner}'
        return self._colorize_frame(border) if use_ansi else border

    def _colorize_frame(self, text: str) -> str:
        """为边框应用默认的柔和浅白强调色。

        Args:
            text (str): 待着色的边框文本。
        Returns:
            str: 着色后的边框文本。
        """
        red, green, blue = self._SOFT_WHITE_RGB
        return f'\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m'

    def _frame_content_line(self, text: str, content_width: int, use_ansi: bool) -> str:
        """构建带左右边框的单行正文。

        Args:
            text (str): 当前正文文本。
            content_width (int): 正文区域的最大宽度。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 已补齐宽度并加上左右边框与内边距的单行文本。
        """
        rendered_text = self._render_content_text(text, content_width, use_ansi)
        left_border = self._colorize_frame('│') if use_ansi else '│'
        right_border = self._colorize_frame('│') if use_ansi else '│'
        horizontal_padding = ' ' * self._frame_horizontal_padding
        return f'{left_border}{horizontal_padding}{rendered_text}{horizontal_padding}{right_border}'

    def _render_content_text(self, text: str, content_width: int, use_ansi: bool) -> str:
        """渲染正文区域中的单行文本。

        Args:
            text (str): 当前正文文本。
            content_width (int): 正文区域的最大宽度。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 已补齐到目标宽度的单行正文文本。
        """
        return text.ljust(content_width)

    def _interpolate_gradient(self, position: float) -> tuple[int, int, int]:
        """在当前类配置的渐变锚点之间执行插值。

        Args:
            position (float): 归一化插值位置，范围通常为 0.0 到 1.0。
        Returns:
            tuple[int, int, int]: 插值后的 RGB 整数三元组。
        """
        if not self._GRADIENT_STOPS:
            return (0, 0, 0)
        if len(self._GRADIENT_STOPS) == 1 or position <= 0:
            return self._GRADIENT_STOPS[0]
        if position >= 1:
            return self._GRADIENT_STOPS[-1]

        segment_count = len(self._GRADIENT_STOPS) - 1
        scaled = position * segment_count
        segment_index = min(int(scaled), segment_count - 1)
        local_ratio = scaled - segment_index
        start = self._GRADIENT_STOPS[segment_index]
        end = self._GRADIENT_STOPS[segment_index + 1]
        return tuple(
            round(start[channel] + (end[channel] - start[channel]) * local_ratio)
            for channel in range(3)
        )

    def _write_blank_lines(self, stream: TextIO, count: int) -> None:
        """向目标流输出指定数量的空行。

        Args:
            stream (TextIO): 目标文本流。
            count (int): 需要输出的空行数量。
        Returns:
            None: 该方法只负责输出换行符。
        """
        for _ in range(count):
            stream.write('\n')

    def _write_line(self, stream: TextIO, text: str) -> None:
        """向目标流输出一行文本。

        Args:
            stream (TextIO): 目标文本流。
            text (str): 待输出的单行文本。
        Returns:
            None: 该方法只负责把文本写入流。
        """
        stream.write(f'{text}\n')