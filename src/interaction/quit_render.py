"""CLI 结束渲染模块。

本模块负责在交互式 CLI 会话结束时输出统一的总结提示框。它只承载结束阶段的
业务排版与着色逻辑，不负责会话汇总建模，也不负责共享的终端渲染基础设施。
"""

from __future__ import annotations

from typing import Literal, TextIO

from core_contracts.interaction_contracts import SessionSummary
from .terminal_render import TerminalRenderer


class ExitRenderer(TerminalRenderer):
    """渲染 CLI 会话结束时的提示框。

    外部通过 render() 传入 SessionSummary，本类会按以下顺序工作：
    1. 组装会话总结正文；
    2. 为正文添加统一边框与可选颜色；
    3. 输出上下外边距与整块提示框。
    """

    _SOFT_WHITE_RGB = (0xE4, 0xE8, 0xF0)  # tuple[int, int, int]: 边框默认使用的柔和白色 RGB。
    _GRADIENT_STOPS = (
        (0x4B, 0x7B, 0xFF),
        (0x22, 0xC5, 0xC7),
        (0xF4, 0x5D, 0x8D),
    )  # tuple[tuple[int, int, int], ...]: 标题与渐变边框使用的蓝-青-粉三段渐变锚点。

    def __init__(
        self,
        *,
        title: str = 'Agent powering down. Goodbye!',
        frame_style: Literal['soft_white', 'gradient'] = 'soft_white',
        top_padding: int = 1,
        bottom_padding: int = 0,
    ) -> None:
        """初始化结束渲染器。

        Args:
            title (str): 提示框第一行标题文本。
            frame_style (Literal['soft_white', 'gradient']): 边框配色方案；soft_white 使用纯浅白，gradient 使用渐变。
            top_padding (int): 提示框上方外边距行数，小于 0 时按 0 处理。
            bottom_padding (int): 提示框下方外边距行数，小于 0 时按 0 处理。
        Returns:
            None: 构造函数只建立渲染器实例状态。
        """
        super().__init__(
            frame_horizontal_padding=1,
            frame_vertical_padding=0,
            top_padding=top_padding,
            bottom_padding=bottom_padding,
        )
        self._title = title  # str: 提示框标题行文本。
        self._frame_style = frame_style  # Literal['soft_white', 'gradient']: 当前实例使用的边框着色模式。

    def render(self, summary: SessionSummary, stream: TextIO | None = None) -> None:
        """将结束总结输出到目标流。

        Args:
            summary (SessionSummary): 待渲染的会话总结对象。
            stream (TextIO | None): 目标文本流；为 None 时默认写入 sys.stdout。
        Returns:
            None: 该方法只负责把提示框写入目标流。
        """
        content_lines = self._build_content_lines(summary)
        self._render_block(content_lines, stream=stream)

    def _build_content_lines(self, summary: SessionSummary) -> tuple[str, ...]:
        """构建提示框正文。

        Args:
            summary (SessionSummary): 当前会话的汇总快照。
        Returns:
            tuple[str, ...]: 供渲染器消费的正文行元组。
        """
        session_text = summary.session_id or 'unavailable'
        tool_summary = f'{summary.tool_calls} (✓ {summary.tool_successes} ✗ {summary.tool_failures})'
        success_rate = f'{summary.success_rate * 100:.1f}%'
        wall_time = self._format_duration(summary.wall_time_seconds)

        lines = [
            self._title,
            '',
            'Interaction Summary',
            f'Session ID:           {session_text}',
            f'Tool Calls:           {tool_summary}',
            f'Success Rate:         {success_rate}',
            '',
            'Performance',
            f'Wall Time:            {wall_time}',
        ]
        if summary.session_id:
            lines.extend(
                [
                    '',
                    f'To resume this session:   agent-resume {summary.session_id}',
                ]
            )
        return tuple(lines)

    def _format_duration(self, seconds: float) -> str:
        """把秒数格式化为紧凑的人类可读文本。

        Args:
            seconds (float): 原始秒数。
        Returns:
            str: 以 s、m s 或 h m s 形式表示的时长文本。
        """
        total_seconds = max(int(round(seconds)), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f'{hours}h {minutes:02d}m {secs:02d}s'
        if minutes:
            return f'{minutes}m {secs:02d}s'
        return f'{secs}s'

    def _colorize_frame(self, text: str) -> str:
        """为边框应用浅白或渐变色。

        Args:
            text (str): 待着色的边框文本。
        Returns:
            str: 着色后的边框文本。
        """
        if self._frame_style == 'gradient':
            return self._colorize_gradient_line(text)
        return super()._colorize_frame(text)

    def _colorize_gradient_line(self, text: str) -> str:
        """为一整行文本应用渐变色。

        Args:
            text (str): 待着色的文本。
        Returns:
            str: 包含 ANSI 渐变色转义码的文本；若文本全为空格则原样返回。
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

    def _render_content_text(self, text: str, content_width: int, use_ansi: bool) -> str:
        """渲染结束提示框正文中的单行文本。

        Args:
            text (str): 当前正文文本。
            content_width (int): 正文区域的最大宽度。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 已补齐宽度并按需着色后的单行正文文本。
        """
        padded_text = self._pad_to_display_width(text, content_width)
        if use_ansi and text == self._title:
            return self._colorize_title(padded_text)
        return padded_text

    def _colorize_title(self, text: str) -> str:
        """为标题应用渐变强调色。

        Args:
            text (str): 待着色的标题文本。
        Returns:
            str: 应用渐变后的标题文本。
        """
        return self._colorize_gradient_line(text)