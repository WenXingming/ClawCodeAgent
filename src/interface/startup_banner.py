# ============================================================
# startup_banner.py
#
# 职责：为 CLI 交互会话渲染启动 Banner。
#
# 核心功能：
#   - 将多行 ASCII-art 文字 + 副标题输出到指定的文本流；
#   - 自动检测目标流是否支持 ANSI 转义码，支持则为每个
#     可见字符渲染从蓝→青→粉的三段线性渐变色；
#   - 所有样式参数（行内容、副标题、间距）均可在构造时覆盖。
# ============================================================

from __future__ import annotations

import os
import sys
from typing import TextIO


class StartupBannerRenderer:
    """CLI 启动 Banner 渲染器。

    将预设（或自定义）的 ASCII-art 大字标题与副标题输出到指定文本流。支持终端 ANSI 真彩色渐变着色，并在不支持
    着色的环境下优雅降级为纯文本。

    核心工作流：
        1. 外部调用 ``render()`` 传入可选的目标流；
        2. 检测流是否支持 ANSI，决定是否着色；
        3. 按「上留白 → 逐行着色标题 → 间距 → 着色副标题 → 下留白」
           的顺序将内容逐行写入流。

    典型用法::

        renderer = StartupBannerRenderer()
        renderer.render()                  # 输出到 stdout
        renderer.render(stream=sys.stderr) # 输出到 stderr
    """

    # ------------------------------------------------------------------
    # 类级常量
    # ------------------------------------------------------------------

    # ASCII-art 大字「TUDOU」六行像素字体
    _DEFAULT_LINES = (
        '████████╗ ██╗   ██╗ ██████╗   ██████╗  ██╗   ██╗',
        '╚══██╔══╝ ██║   ██║ ██╔══██╗ ██╔═══██╗ ██║   ██║',
        '   ██║    ██║   ██║ ██║  ██║ ██║   ██║ ██║   ██║',
        '   ██║    ██║   ██║ ██║  ██║ ██║   ██║ ██║   ██║',
        '   ██║    ╚██████╔╝ ██████╔╝ ╚██████╔╝ ╚██████╔╝',
        '   ╚═╝     ╚═════╝  ╚═════╝   ╚═════╝   ╚═════╝ ',
    )

    _DEFAULT_SUBTITLE = 'Tudou Code Agent - Empower Your Coding Journey with AI\nVersion 1.0.0'
    _FRAME_HORIZONTAL_PADDING = 2
    _FRAME_VERTICAL_PADDING = 1

    # 渐变色三个锚点：蓝 → 青 → 粉（RGB 整数元组）
    _SOFT_WHITE_RGB = (0xE4, 0xE8, 0xF0)
    _GRADIENT_STOPS = (
        (0x4B, 0x7B, 0xFF),  # 蓝
        (0x22, 0xC5, 0xC7),  # 青
        (0xF4, 0x5D, 0x8D),  # 粉
    )

    # ------------------------------------------------------------------
    # 构造函数
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        lines: tuple[str, ...] | None = None,
        subtitle: str | None = None,
        top_padding: int = 1,
        gap_before_subtitle: int = 1,
        bottom_padding: int = 1,
    ) -> None:
        """初始化 Banner 渲染器，所有参数均为关键字参数。
        Args:
            lines (tuple[str, ...] | None): 自定义的 ASCII-art 行列表；
                为 None 时使用 ``_DEFAULT_LINES``。
            subtitle (str | None): 副标题文字；为 None 时使用
                ``_DEFAULT_SUBTITLE``。
            top_padding (int): 标题上方空行数，最小值为 0。
            gap_before_subtitle (int): 标题与副标题之间的空行数，最小值为 0。
            bottom_padding (int): 副标题下方空行数，最小值为 0。
        """
        self._lines = lines or self._DEFAULT_LINES               # tuple[str, ...]：要渲染的 ASCII-art 行
        self._subtitle = subtitle or self._DEFAULT_SUBTITLE      # str：副标题文字
        self._top_padding = max(top_padding, 0)                  # int：标题上方空行数（≥0）
        self._gap_before_subtitle = max(gap_before_subtitle, 0)  # int：标题与副标题间空行数（≥0）
        self._bottom_padding = max(bottom_padding, 0)            # int：副标题下方空行数（≥0）

    # ------------------------------------------------------------------
    # 公有接口
    # ------------------------------------------------------------------

    def render(self, stream: TextIO | None = None) -> None:
        """将完整 Banner 输出到目标流。

        输出顺序：上方留白 → 圆角矩形边框 → 标题与副标题内容
        （含着色）→ 圆角矩形边框 → 下方留白。

        Args:
            stream (TextIO | None): 目标文本流；为 None 时默认使用
                ``sys.stdout``。
        Returns:
            None
        """
        target = stream or sys.stdout
        use_ansi = self._stream_supports_ansi(target)
        framed_lines = self._build_framed_lines(use_ansi)

        self._write_blank_lines(target, self._top_padding)
        for line in framed_lines:
            self._write_line(target, line)
        self._write_blank_lines(target, self._bottom_padding)

    # ------------------------------------------------------------------
    # 私有辅助函数（按深度优先调用顺序排列）
    # ------------------------------------------------------------------

    def _stream_supports_ansi(self, stream: TextIO) -> bool:
        """检测给定流是否支持 ANSI 转义码着色。

        检测优先级：
        1. 环境变量 ``NO_COLOR`` 非空时，强制禁用；
        2. 流没有 ``isatty`` 方法或返回 False 时，禁用；
        3. 非 Windows 系统直接启用；
        4. Windows 下检查常见的支持 ANSI 的终端环境变量。

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

    def _write_blank_lines(self, stream: TextIO, count: int) -> None:
        """向流中写入指定数量的空行。
        Args:
            stream (TextIO): 目标文本流。
            count (int): 要写入的空行数量；为 0 时不写入任何内容。
        Returns:
            None
        """
        for _ in range(count):
            stream.write('\n')

    def _build_framed_lines(self, use_ansi: bool) -> tuple[str, ...]:
        """构建包含圆角边框的完整 Banner 行列表。"""
        content_lines = self._apply_vertical_padding(self._build_content_lines())
        content_width = max((len(line) for line in content_lines), default=0)
        inner_width = content_width + self._FRAME_HORIZONTAL_PADDING * 2
        rendered_lines = [self._frame_border_line(inner_width, top=True, use_ansi=use_ansi)]
        rendered_lines.extend(
            self._frame_content_line(line, content_width, use_ansi)
            for line in content_lines
        )
        rendered_lines.append(self._frame_border_line(inner_width, top=False, use_ansi=use_ansi))
        return tuple(rendered_lines)

    def _apply_vertical_padding(self, content_lines: tuple[str, ...]) -> tuple[str, ...]:
        """在边框内容上下追加空白行。"""
        vertical_padding = ('',) * self._FRAME_VERTICAL_PADDING
        return (*vertical_padding, *content_lines, *vertical_padding)

    def _build_content_lines(self) -> tuple[str, ...]:
        """组装边框内部的所有内容行。"""
        subtitle_lines = tuple(self._subtitle.splitlines()) or ('',)
        return (
            *self._lines,
            *([''] * self._gap_before_subtitle),
            *subtitle_lines,
        )

    def _frame_border_line(self, inner_width: int, *, top: bool, use_ansi: bool) -> str:
        """生成顶部或底部圆角边框。"""
        left_corner, right_corner = ('╭', '╮') if top else ('╰', '╯')
        border = f'{left_corner}{"─" * inner_width}{right_corner}'
        return self._colorize_frame(border) if use_ansi else border

    def _frame_content_line(self, text: str, content_width: int, use_ansi: bool) -> str:
        """生成带左右边框的内容行。"""
        padded_text = text.ljust(content_width)
        rendered_text = self._colorize_line(padded_text) if use_ansi else padded_text
        left_border = self._colorize_frame('│') if use_ansi else '│'
        right_border = self._colorize_frame('│') if use_ansi else '│'
        horizontal_padding = ' ' * self._FRAME_HORIZONTAL_PADDING
        return f'{left_border}{horizontal_padding}{rendered_text}{horizontal_padding}{right_border}'

    def _colorize_frame(self, text: str) -> str:
        """为边框应用统一的强调色。"""
        # red, green, blue = self._GRADIENT_STOPS[1]
        red, green, blue = self._SOFT_WHITE_RGB
        return f'\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m'

    def _colorize_line(self, text: str) -> str:
        """为文本中的每个可见字符附加渐变色 ANSI 转义码。

        空格字符不参与着色计数，直接原样保留；
        渐变色区间由 ``_GRADIENT_STOPS`` 定义。
        行尾附加重置码 ``\\x1b[0m`` 以防颜色溢出到下一行。

        Args:
            text (str): 待着色的原始文本。
        Returns:
            str: 包含 ANSI 24-bit 真彩色转义码的着色字符串；
                若文本全为空格则原样返回。
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
        """在 ``_GRADIENT_STOPS`` 定义的多段渐变中插值求 RGB 颜色。

        将 [0, 1] 的归一化位置映射到对应颜色段，并在段内做线性插值。
        边界值（≤0 或 ≥1）直接返回首/尾锚点颜色，无需插值。

        Args:
            position (float): 归一化插值位置，范围 [0.0, 1.0]；
                0.0 对应第一个锚点，1.0 对应最后一个锚点。
        Returns:
            tuple[int, int, int]: 插值后的 (R, G, B) 整数三元组，
                每个分量范围 [0, 255]。
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

    def _write_line(self, stream: TextIO, text: str) -> None:
        """向流中写入一行文本（末尾自动追加换行符）。
        Args:
            stream (TextIO): 目标文本流。
            text (str): 要写入的文本内容，可含 ANSI 转义码。
        Returns:
            None
        """
        stream.write(f'{text}\n')