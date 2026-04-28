"""CLI 启动 Banner 渲染模块。

本模块负责在交互式命令行会话启动时输出统一的欢迎横幅，主要职责包括：
1. 组织 ASCII-art 标题与多行副标题内容；
2. 检测终端是否支持 ANSI 真彩色；
3. 在支持时为标题内容应用渐变色，并用圆角边框包裹整体布局。
"""

from __future__ import annotations

import os
import sys
from typing import TextIO


class StartupBannerRenderer:
    """CLI 启动 Banner 渲染器。

    外部通过 render() 触发完整输出流程，本类会先准备标题与副标题内容，
    再根据终端能力决定是否着色，最后生成带圆角边框的横幅文本并写入目标流。

    颜色与默认文案属于类级共享样式；上下留白与边框内边距属于具体渲染器实例的布局状态。
    """

    _DEFAULT_LINES = (
        '████████╗ ██╗   ██╗ ██████╗   ██████╗  ██╗   ██╗',
        '╚══██╔══╝ ██║   ██║ ██╔══██╗ ██╔═══██╗ ██║   ██║',
        '   ██║    ██║   ██║ ██║  ██║ ██║   ██║ ██║   ██║',
        '   ██║    ██║   ██║ ██║  ██║ ██║   ██║ ██║   ██║',
        '   ██║    ╚██████╔╝ ██████╔╝ ╚██████╔╝ ╚██████╔╝',
        '   ╚═╝     ╚═════╝  ╚═════╝   ╚═════╝   ╚═════╝ ',
    )  # tuple[str, ...]: 默认 ASCII-art 标题各行文本。

    _DEFAULT_SUBTITLE = 'Tudou Code Agent - Empower Your Coding Journey with AI\nVersion 1.0.0'
    # str: 默认副标题，允许使用换行拆成多行展示。

    _SOFT_WHITE_RGB = (0xE4, 0xE8, 0xF0)  # tuple[int, int, int]: 边框使用的柔和白色 RGB。
    _GRADIENT_STOPS = (
        (0x4B, 0x7B, 0xFF),  # 蓝
        (0x22, 0xC5, 0xC7),  # 青
        (0xF4, 0x5D, 0x8D),  # 粉
    )  # tuple[tuple[int, int, int], ...]: 标题文字渐变使用的蓝-青-粉锚点。

    def __init__(
        self,
        *,
        lines: tuple[str, ...] | None = None,
        subtitle: str | None = None,
        top_padding: int = 1,
        gap_before_subtitle: int = 1,
        bottom_padding: int = 1,
    ) -> None:
        """初始化 Banner 渲染器。

        Args:
            lines (tuple[str, ...] | None): 自定义 ASCII-art 行列表；为 None 时使用默认标题。
            subtitle (str | None): 自定义副标题文本；为 None 时使用默认副标题。
            top_padding (int): 标题上方空行数，最小值为 0。
            gap_before_subtitle (int): 标题与副标题之间的空行数，最小值为 0。
            bottom_padding (int): 副标题下方空行数，最小值为 0。
        Returns:
            None: 构造函数只初始化渲染器状态。
        """
        self._lines = tuple(lines or self._DEFAULT_LINES)  # tuple[str, ...]: 本实例要渲染的标题行。
        self._subtitle = subtitle or self._DEFAULT_SUBTITLE  # str: 本实例要渲染的副标题原文。
        self._frame_horizontal_padding = 2  # int: 边框左右内边距，单位为字符。
        self._frame_vertical_padding = 1  # int: 边框上下内边距，单位为字符行数。
        self._top_padding = max(top_padding, 0)  # int: 横幅上方外边距行数。
        self._gap_before_subtitle = max(gap_before_subtitle, 0)  # int: 标题与副标题之间的空行数。
        self._bottom_padding = max(bottom_padding, 0)  # int: 横幅下方外边距行数。

    def render(self, stream: TextIO | None = None) -> None:
        """将完整 Banner 输出到目标流。

        Args:
            stream (TextIO | None): 目标文本流；为 None 时默认使用 sys.stdout。
        Returns:
            None: 该方法只负责把完整横幅写入目标流。
        """
        target = stream or sys.stdout
        use_ansi = self._stream_supports_ansi(target)
        framed_lines = self._build_framed_lines(use_ansi)

        self._write_blank_lines(target, self._top_padding)
        for line in framed_lines:
            self._write_line(target, line)
        self._write_blank_lines(target, self._bottom_padding)

    def _stream_supports_ansi(self, stream: TextIO) -> bool:
        """检测给定流是否支持 ANSI 转义码着色。

        Args:
            stream (TextIO): 待检测的文本流对象。
        Returns:
            bool: True 表示支持 ANSI 着色，False 表示不支持。
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

    def _build_framed_lines(self, use_ansi: bool) -> tuple[str, ...]:
        """构建包含圆角边框的完整 Banner 行列表。

        Args:
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            tuple[str, ...]: 适合直接逐行输出的完整横幅文本。
        """
        content_lines = self._apply_vertical_padding(self._build_content_lines())
        content_width = max((len(line) for line in content_lines), default=0)
        inner_width = content_width + self._frame_horizontal_padding * 2
        rendered_lines = [self._frame_border_line(inner_width, top=True, use_ansi=use_ansi)]
        rendered_lines.extend(
            self._frame_content_line(line, content_width, use_ansi)
            for line in content_lines
        )
        rendered_lines.append(self._frame_border_line(inner_width, top=False, use_ansi=use_ansi))
        return tuple(rendered_lines)

    def _apply_vertical_padding(self, content_lines: tuple[str, ...]) -> tuple[str, ...]:
        """在边框内容上下追加空白行。

        Args:
            content_lines (tuple[str, ...]): 原始正文行。
        Returns:
            tuple[str, ...]: 追加上下边框内边距后的正文行。
        """
        vertical_padding = ('',) * self._frame_vertical_padding
        return (*vertical_padding, *content_lines, *vertical_padding)

    def _build_content_lines(self) -> tuple[str, ...]:
        """组装边框内部的所有内容行。

        Args:
            None: 该方法直接读取实例配置。
        Returns:
            tuple[str, ...]: 标题、多行副标题及其间距拼装后的正文行。
        """
        subtitle_lines = tuple(self._subtitle.splitlines()) or ('',)
        return (
            *self._lines,
            *([''] * self._gap_before_subtitle),
            *subtitle_lines,
        )

    def _frame_border_line(self, inner_width: int, *, top: bool, use_ansi: bool) -> str:
        """生成顶部或底部圆角边框。

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
        """为边框应用统一的强调色。

        Args:
            text (str): 待着色的边框文本。
        Returns:
            str: 使用柔和白色着色后的边框文本。
        """
        red, green, blue = self._SOFT_WHITE_RGB
        return f'\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m'

    def _frame_content_line(self, text: str, content_width: int, use_ansi: bool) -> str:
        """生成带左右边框的内容行。

        Args:
            text (str): 当前正文文本。
            content_width (int): 正文区域的最大宽度。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 已补齐宽度、加上左右边框与内边距的单行文本。
        """
        padded_text = text.ljust(content_width)
        rendered_text = self._colorize_line(padded_text) if use_ansi else padded_text
        left_border = self._colorize_frame('│') if use_ansi else '│'
        right_border = self._colorize_frame('│') if use_ansi else '│'
        horizontal_padding = ' ' * self._frame_horizontal_padding
        return f'{left_border}{horizontal_padding}{rendered_text}{horizontal_padding}{right_border}'

    def _colorize_line(self, text: str) -> str:
        """为文本中的每个可见字符附加渐变色 ANSI 转义码。

        Args:
            text (str): 待着色的原始文本。
        Returns:
            str: 包含 ANSI 真彩色转义码的着色字符串；若文本全为空格则原样返回。
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

            rgb = self._interpolate_gradient(visible_index / total)
            rendered.append(f'\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{char}')
            visible_index += 1
        rendered.append('\x1b[0m')
        return ''.join(rendered)

    def _interpolate_gradient(self, position: float) -> tuple[int, int, int]:
        """在渐变锚点之间插值求 RGB 颜色。

        Args:
            position (float): 归一化插值位置，范围通常为 0.0 到 1.0。
        Returns:
            tuple[int, int, int]: 插值后的 RGB 整数三元组。
        """
        if position <= 0:
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
        """向流中写入指定数量的空行。

        Args:
            stream (TextIO): 目标文本流。
            count (int): 要写入的空行数量。
        Returns:
            None: 该方法只向流中写入换行符。
        """
        for _ in range(count):
            stream.write('\n')

    def _write_line(self, stream: TextIO, text: str) -> None:
        """向流中写入一行文本。

        Args:
            stream (TextIO): 目标文本流。
            text (str): 要写入的文本内容，可含 ANSI 转义码。
        Returns:
            None: 该方法只负责输出一行并补充换行符。
        """
        stream.write(f'{text}\n')