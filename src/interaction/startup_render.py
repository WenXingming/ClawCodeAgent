"""CLI 启动渲染模块。

本模块负责在交互式命令行会话启动时输出欢迎横幅，并在可用时追加一行
环境摘要文本。它只承载启动阶段的业务排版与着色逻辑，不负责摘要建模
或共享的终端渲染基础设施。
"""

from __future__ import annotations

import sys
from typing import TextIO

from interaction.environment_summary import EnvironmentLoadSummary
from interaction.terminal_render import TerminalRenderer


class StartupRenderer(TerminalRenderer):
    """渲染 CLI 启动阶段的欢迎横幅。

    外部通过 render() 触发完整输出流程。本类会准备 ASCII-art 标题与副标题，
    再根据终端能力决定是否应用 ANSI 渐变，最后输出带圆角边框的启动横幅，
    并在需要时追加一行独立的环境加载摘要。
    """

    _DEFAULT_LINES = (
        '████████╗ ██╗   ██╗ ██████╗   ██████╗  ██╗   ██╗',
        '╚══██╔══╝ ██║   ██║ ██╔══██╗ ██╔═══██╗ ██║   ██║',
        '   ██║    ██║   ██║ ██║  ██║ ██║   ██║ ██║   ██║',
        '   ██║    ██║   ██║ ██║  ██║ ██║   ██║ ██║   ██║',
        '   ██║    ╚██████╔╝ ██████╔╝ ╚██████╔╝ ╚██████╔╝',
        '   ╚═╝     ╚═════╝  ╚═════╝   ╚═════╝   ╚═════╝ ',
    )  # tuple[str, ...]: 默认 ASCII-art 标题各行文本。
    _DEFAULT_SUBTITLE = 'Tudou Code Agent - Empower Your Coding Journey with AI\n\nVersion 1.0.0'
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
        """初始化启动渲染器。

        Args:
            lines (tuple[str, ...] | None): 自定义 ASCII-art 行列表；为 None 时使用默认标题。
            subtitle (str | None): 自定义副标题文本；为 None 时使用默认副标题。
            top_padding (int): 横幅上方空行数，最小值为 0。
            gap_before_subtitle (int): 标题与副标题之间的空行数，最小值为 0。
            bottom_padding (int): 横幅下方空行数，最小值为 0。
        Returns:
            None: 构造函数只初始化渲染器状态。
        """
        super().__init__(
            frame_horizontal_padding=2,
            frame_vertical_padding=1,
            top_padding=top_padding,
            bottom_padding=bottom_padding,
        )
        self._lines = tuple(lines or self._DEFAULT_LINES)  # tuple[str, ...]: 本实例要渲染的标题行。
        self._subtitle = subtitle or self._DEFAULT_SUBTITLE  # str: 本实例要渲染的副标题原文。
        self._gap_before_subtitle = max(gap_before_subtitle, 0)  # int: 标题与副标题之间的空行数。

    def render(
        self,
        stream: TextIO | None = None,
        *,
        environment_summary: EnvironmentLoadSummary | None = None,
    ) -> None:
        """将完整启动横幅与可选环境摘要输出到目标流。

        Args:
            stream (TextIO | None): 目标文本流；为 None 时默认使用 sys.stdout。
            environment_summary (EnvironmentLoadSummary | None): 可选的环境加载摘要；为 None 时不输出摘要行。
        Returns:
            None: 该方法只负责把完整横幅写入目标流。
        """
        target = stream or sys.stdout
        use_ansi = self._stream_supports_ansi(target)
        content_lines = self._build_content_lines()
        environment_lines = self._build_environment_lines(environment_summary)

        self._write_blank_lines(target, self._top_padding)
        self._render_frame(target, content_lines, use_ansi=use_ansi)
        if environment_lines:
            self._write_blank_lines(target, 1)
            for line in environment_lines:
                self._write_line(target, line)
        self._write_blank_lines(target, self._bottom_padding)

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

    @staticmethod
    def _build_environment_lines(environment_summary: EnvironmentLoadSummary | None) -> tuple[str, ...]:
        """构建横幅下方的环境摘要行。

        Args:
            environment_summary (EnvironmentLoadSummary | None): 启动阶段的环境加载摘要对象。
        Returns:
            tuple[str, ...]: 需要追加输出的环境摘要行；没有可展示内容时返回空元组。
        """
        if environment_summary is None:
            return ()
        summary_line = environment_summary.render_line()
        if not summary_line:
            return ()
        return (summary_line,)

    def _render_content_text(self, text: str, content_width: int, use_ansi: bool) -> str:
        """渲染启动横幅正文中的单行文本。

        Args:
            text (str): 当前正文文本。
            content_width (int): 正文区域的最大宽度。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 已补齐宽度并按需着色后的单行正文文本。
        """
        padded_text = self._pad_to_display_width(text, content_width)
        if not use_ansi:
            return padded_text
        return self._colorize_line(padded_text)

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