"""终端渲染基础设施模块（interaction 包内部实现）。

本模块属于 interaction 包的内部实现，外部代码禁止直接导入。
所有功能通过 interaction.interaction_gateway.InteractionGateway 对外暴露。

当前承载的核心能力（按深度优先调用链排列）：
1. ANSI 能力检测（_stream_supports_ansi）；
2. 空行与单行输出（_write_blank_lines / _write_line）；
3. 带圆角边框的终端块渲染（_render_block → _render_frame → _build_framed_lines）；
4. 显示宽度计算与渐变色插值（底层工具方法）。
"""

from __future__ import annotations

import os
import sys
import unicodedata
from typing import TextIO


class TerminalRenderer:
    """为 interaction 层终端输出提供共享的渲染基础设施。

    子类继承本类并负责：
    - 调用 _render_block() 触发完整渲染流程；
    - 按需覆盖 _render_content_text() 以定制正文着色策略；
    - 按需覆盖 _colorize_frame() 以定制边框着色策略。

    本类不持有任何可变运行时状态，所有渲染参数通过 __init__ 一次注入。
    """

    _SOFT_WHITE_RGB = (0xE4, 0xE8, 0xF0)
    # tuple[int, int, int]: 边框默认使用的柔和白色 RGB（浅白强调色）。
    _GRADIENT_STOPS = (
        (0x4B, 0x7B, 0xFF),
        (0x22, 0xC5, 0xC7),
        (0xF4, 0x5D, 0x8D),
    )
    # tuple[tuple[int, int, int], ...]: 默认渐变颜色锚点序列（蓝-青-粉）。

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
            frame_horizontal_padding (int): 边框左右内边距字符数；负值按 0 处理。
            frame_vertical_padding (int): 边框上下内边距行数；负值按 0 处理。
            top_padding (int): 整块内容上方外边距行数；负值按 0 处理。
            bottom_padding (int): 整块内容下方外边距行数；负值按 0 处理。
        Returns:
            None: 构造函数只建立渲染配置状态，不产生任何 I/O。
        """
        self._frame_horizontal_padding = max(frame_horizontal_padding, 0)
        # int: 边框左右内边距字符数。
        self._frame_vertical_padding = max(frame_vertical_padding, 0)
        # int: 边框上下内边距行数。
        self._top_padding = max(top_padding, 0)
        # int: 整块内容上方外边距行数。
        self._bottom_padding = max(bottom_padding, 0)
        # int: 整块内容下方外边距行数。

    # ─────────────────────────────────────────────────────────
    # 核心渲染入口（深度优先排列）
    # ─────────────────────────────────────────────────────────

    def _render_block(self, content_lines: tuple[str, ...], stream: TextIO | None = None) -> None:
        """将给定正文行渲染为完整的带边框终端块。

        这是子类触发渲染的主入口。完整流程：
          ANSI 检测 → 上外边距 → 带边框帧 → 下外边距。

        Args:
            content_lines (tuple[str, ...]): 需要被渲染的正文行元组。
            stream (TextIO | None): 目标文本流；None 时默认使用 sys.stdout。
        Returns:
            None: 该方法只负责将终端块写入目标流。
        """
        target = stream or sys.stdout
        use_ansi = self._stream_supports_ansi(target)
        self._write_blank_lines(target, self._top_padding)
        self._render_frame(target, content_lines, use_ansi=use_ansi)
        self._write_blank_lines(target, self._bottom_padding)

    def _stream_supports_ansi(self, stream: TextIO) -> bool:
        """检测目标流是否支持 ANSI 转义序列。

        优先尊重 NO_COLOR 环境变量；非 TTY 流直接返回 False；
        Windows 上额外检查已知 ANSI 兼容终端的环境变量。

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

    def _write_blank_lines(self, stream: TextIO, count: int) -> None:
        """向目标流输出指定数量的空行（外边距使用）。

        Args:
            stream (TextIO): 目标文本流。
            count (int): 需要输出的空行数量；为 0 时为空操作。
        Returns:
            None: 该方法只负责写入换行符。
        """
        for _ in range(count):
            stream.write('\n')

    def _render_frame(
        self,
        stream: TextIO,
        content_lines: tuple[str, ...],
        *,
        use_ansi: bool,
    ) -> None:
        """将正文行渲染为带圆角边框的完整帧并写入目标流。

        Args:
            stream (TextIO): 目标文本流。
            content_lines (tuple[str, ...]): 需要被框住的正文行元组。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            None: 该方法只负责将带边框的内容行逐行写入流。
        """
        framed_lines = self._build_framed_lines(content_lines, use_ansi)
        for line in framed_lines:
            self._write_line(stream, line)

    def _build_framed_lines(
        self,
        content_lines: tuple[str, ...],
        use_ansi: bool,
    ) -> tuple[str, ...]:
        """构建包含顶部边框、内容行、底部边框的完整帧行元组。

        流程：垂直内边距填充 → 计算内容宽度 → 顶部边框 → 内容行 → 底部边框。

        Args:
            content_lines (tuple[str, ...]): 原始正文行。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            tuple[str, ...]: 含完整边框与内边距的文本行元组。
        """
        padded_lines = self._apply_vertical_padding(content_lines)
        content_width = max((self._display_width(line) for line in padded_lines), default=0)
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
            tuple[str, ...]: 追加上下内边距空白行后的正文行元组。
        """
        vertical_padding = ('',) * self._frame_vertical_padding
        return (*vertical_padding, *content_lines, *vertical_padding)

    def _frame_border_line(self, inner_width: int, *, top: bool, use_ansi: bool) -> str:
        """构建顶部或底部圆角边框行。

        Args:
            inner_width (int): 边框内部宽度（不含左右角字符）。
            top (bool): True 生成顶部边框（╭...╮），False 生成底部边框（╰...╯）。
            use_ansi (bool): 是否对边框应用 ANSI 着色。
        Returns:
            str: 已按需着色的单行边框文本。
        """
        left_corner, right_corner = ('╭', '╮') if top else ('╰', '╯')
        border = f'{left_corner}{"─" * inner_width}{right_corner}'
        return self._colorize_frame(border) if use_ansi else border

    def _colorize_frame(self, text: str) -> str:
        """为边框文本应用柔和浅白 ANSI 强调色。

        子类可覆盖此方法以定制边框着色策略（如渐变色）。

        Args:
            text (str): 待着色的边框文本。
        Returns:
            str: 包含 ANSI 真彩色转义码的边框文本。
        """
        red, green, blue = self._SOFT_WHITE_RGB
        return f'\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m'

    def _frame_content_line(self, text: str, content_width: int, use_ansi: bool) -> str:
        """构建带左右边框与内边距的单行正文。

        Args:
            text (str): 当前正文文本（未补齐宽度）。
            content_width (int): 正文区域的目标宽度（按显示宽度计算）。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 已补齐宽度、加上左右边框与水平内边距的单行文本。
        """
        rendered_text = self._render_content_text(text, content_width, use_ansi)
        left_border = self._colorize_frame('│') if use_ansi else '│'
        right_border = self._colorize_frame('│') if use_ansi else '│'
        horizontal_padding = ' ' * self._frame_horizontal_padding
        return f'{left_border}{horizontal_padding}{rendered_text}{horizontal_padding}{right_border}'

    def _render_content_text(self, text: str, content_width: int, use_ansi: bool) -> str:
        """渲染正文区域中的单行文本（基类版本：仅补齐宽度，不着色）。

        子类可覆盖此方法以实现标题渐变色、关键词高亮等效果。

        Args:
            text (str): 当前正文文本。
            content_width (int): 正文区域的目标宽度。
            use_ansi (bool): 是否启用 ANSI 着色（基类忽略此参数）。
        Returns:
            str: 已补齐到目标宽度的单行正文文本。
        """
        del use_ansi
        return self._pad_to_display_width(text, content_width)

    def _pad_to_display_width(self, text: str, target_width: int) -> str:
        """按终端显示宽度在文本右侧补充空格至目标宽度。

        Args:
            text (str): 待补齐的原始文本。
            target_width (int): 目标显示宽度（字符数）。
        Returns:
            str: 右侧补齐空格后的文本；原始宽度已达目标时原样返回。
        """
        padding_width = max(target_width - self._display_width(text), 0)
        return f'{text}{" " * padding_width}'

    def _display_width(self, text: str) -> int:
        """估算文本在终端中的总显示宽度（字符数）。

        Args:
            text (str): 待估算的文本。
        Returns:
            int: 各字符显示宽度之和（CJK 宽字符计 2，控制字符计 0）。
        """
        return sum(self._character_display_width(char) for char in text)

    @staticmethod
    def _character_display_width(char: str) -> int:
        """估算单个字符在终端中的显示宽度。

        Args:
            char (str): 待估算的单个字符。
        Returns:
            int: 0（控制字符/组合字符）、1（普通字符）或 2（CJK 宽字符）。
        """
        if not char:
            return 0
        if char == '\t':
            return 4
        if unicodedata.combining(char):
            return 0
        category = unicodedata.category(char)
        if category in {'Cc', 'Cf'}:
            return 0
        if unicodedata.east_asian_width(char) in {'F', 'W'}:
            return 2
        return 1

    def _write_line(self, stream: TextIO, text: str) -> None:
        """向目标流输出一行文本（追加换行符）。

        Args:
            stream (TextIO): 目标文本流。
            text (str): 待输出的单行文本（不含换行符）。
        Returns:
            None: 该方法只负责将文本与换行符写入流。
        """
        stream.write(f'{text}\n')

    def _interpolate_gradient(self, position: float) -> tuple[int, int, int]:
        """在当前类配置的渐变锚点之间执行线性插值。

        供子类的着色方法（如 _colorize_gradient_line）调用。

        Args:
            position (float): 归一化插值位置，范围通常为 0.0 到 1.0。
        Returns:
            tuple[int, int, int]: 插值后的 RGB 整数三元组，各分量范围 0–255。
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
        content_width = max((self._display_width(line) for line in padded_lines), default=0)
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
        del use_ansi
        return self._pad_to_display_width(text, content_width)

    def _pad_to_display_width(self, text: str, target_width: int) -> str:
        """按终端显示宽度补齐单行文本。"""
        padding_width = max(target_width - self._display_width(text), 0)
        return f'{text}{" " * padding_width}'

    def _display_width(self, text: str) -> int:
        """估算文本在终端中的显示宽度。"""
        return sum(self._character_display_width(char) for char in text)

    @staticmethod
    def _character_display_width(char: str) -> int:
        """估算单个字符在终端中的显示宽度。"""
        if not char:
            return 0
        if char == '\t':
            return 4
        if unicodedata.combining(char):
            return 0
        category = unicodedata.category(char)
        if category in {'Cc', 'Cf'}:
            return 0
        if unicodedata.east_asian_width(char) in {'F', 'W'}:
            return 2
        return 1

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