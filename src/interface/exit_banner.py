"""CLI 会话结束提示框渲染器。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, TextIO


@dataclass
class SessionExitSummary:
    """表示一次 CLI 交互结束时的汇总信息。"""

    session_id: str | None = None
    tool_calls: int = 0
    tool_successes: int = 0
    tool_failures: int = 0
    wall_time_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        """返回工具调用成功率。"""
        if self.tool_calls <= 0:
            return 0.0
        return self.tool_successes / self.tool_calls


@dataclass
class SessionInteractionTracker:
    """维护一次 CLI interaction 的累计统计。"""

    session_id: str | None = None
    started_at: float = 0.0
    tool_calls: int = 0
    tool_successes: int = 0
    tool_failures: int = 0

    @classmethod
    def start(cls, session_id: str | None = None) -> 'SessionInteractionTracker':
        """创建新的交互汇总状态。"""
        return cls(session_id=session_id, started_at=perf_counter())

    def observe_tool_result(self, *, ok: bool) -> None:
        """累计一次工具结果。"""
        self.tool_calls += 1
        if ok:
            self.tool_successes += 1
            return
        self.tool_failures += 1

    def update_session_id(self, session_id: str | None) -> None:
        """刷新最后一个已知的活动 session id。"""
        if session_id:
            self.session_id = session_id

    def to_summary(self) -> SessionExitSummary:
        """将累计状态投影为可渲染的总结对象。"""
        return SessionExitSummary(
            session_id=self.session_id,
            tool_calls=self.tool_calls,
            tool_successes=self.tool_successes,
            tool_failures=self.tool_failures,
            wall_time_seconds=max(perf_counter() - self.started_at, 0.0),
        )


class SessionExitSummaryRenderer:
    """渲染 CLI 结束提示框。"""

    _SOFT_WHITE_RGB = (0xE4, 0xE8, 0xF0)
    _GRADIENT_STOPS = (
        (0x4B, 0x7B, 0xFF),
        (0x22, 0xC5, 0xC7),
        (0xF4, 0x5D, 0x8D),
    )
    _FRAME_HORIZONTAL_PADDING = 1
    _FRAME_VERTICAL_PADDING = 0

    def __init__(
        self,
        *,
        title: str = 'Agent powering down. Goodbye!',
        frame_style: Literal['soft_white', 'gradient'] = 'soft_white',
        top_padding: int = 1,
        bottom_padding: int = 0,
    ) -> None:
        self._title = title
        self._frame_style = frame_style
        self._top_padding = max(top_padding, 0)
        self._bottom_padding = max(bottom_padding, 0)

    def render(self, summary: SessionExitSummary, stream: TextIO | None = None) -> None:
        """将结束总结输出到目标流。"""
        target = stream or sys.stdout
        use_ansi = self._stream_supports_ansi(target)
        content_lines = self._build_content_lines(summary)
        framed_lines = self._build_framed_lines(content_lines, use_ansi)

        self._write_blank_lines(target, self._top_padding)
        for line in framed_lines:
            self._write_line(target, line)
        self._write_blank_lines(target, self._bottom_padding)

    def _build_content_lines(self, summary: SessionExitSummary) -> tuple[str, ...]:
        """构建提示框正文。"""
        session_text = summary.session_id or 'unavailable'
        tool_summary = f'{summary.tool_calls} (✓ {summary.tool_successes} ✗ {summary.tool_failures})'
        success_rate = f'{summary.success_rate * 100:.1f}%'
        wall_time = self._format_duration(summary.wall_time_seconds)

        lines = [
            self._title,
            '',
            'Interaction Summary',
            f'Session ID:   {session_text}',
            f'Tool Calls:   {tool_summary}',
            f'Success Rate: {success_rate}',
            '',
            'Performance',
            f'Wall Time:    {wall_time}',
        ]
        if summary.session_id:
            lines.extend(
                [
                    '',
                    f'To resume this session: agent-resume {summary.session_id}',
                ]
            )
        return tuple(lines)

    def _build_framed_lines(self, content_lines: tuple[str, ...], use_ansi: bool) -> tuple[str, ...]:
        """构建带圆角边框的提示框。"""
        padded_lines = self._apply_vertical_padding(content_lines)
        content_width = max((len(line) for line in padded_lines), default=0)
        inner_width = content_width + self._FRAME_HORIZONTAL_PADDING * 2
        rendered_lines = [self._frame_border_line(inner_width, top=True, use_ansi=use_ansi)]
        rendered_lines.extend(
            self._frame_content_line(line, content_width, use_ansi)
            for line in padded_lines
        )
        rendered_lines.append(self._frame_border_line(inner_width, top=False, use_ansi=use_ansi))
        return tuple(rendered_lines)

    def _apply_vertical_padding(self, content_lines: tuple[str, ...]) -> tuple[str, ...]:
        """在边框内容上下追加空白行。"""
        vertical_padding = ('',) * self._FRAME_VERTICAL_PADDING
        return (*vertical_padding, *content_lines, *vertical_padding)

    def _frame_border_line(self, inner_width: int, *, top: bool, use_ansi: bool) -> str:
        """生成顶部或底部边框。"""
        left_corner, right_corner = ('╭', '╮') if top else ('╰', '╯')
        border = f'{left_corner}{"─" * inner_width}{right_corner}'
        return self._colorize_frame(border) if use_ansi else border

    def _frame_content_line(self, text: str, content_width: int, use_ansi: bool) -> str:
        """生成一行正文。"""
        padded_text = text.ljust(content_width)
        rendered_text = self._colorize_title(padded_text) if use_ansi and text == self._title else padded_text
        left_border = self._colorize_frame('│') if use_ansi else '│'
        right_border = self._colorize_frame('│') if use_ansi else '│'
        horizontal_padding = ' ' * self._FRAME_HORIZONTAL_PADDING
        return f'{left_border}{horizontal_padding}{rendered_text}{horizontal_padding}{right_border}'

    def _stream_supports_ansi(self, stream: TextIO) -> bool:
        """检测目标流是否支持 ANSI。"""
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

    def _colorize_frame(self, text: str) -> str:
        """为边框应用浅白或渐变色。"""
        if self._frame_style == 'gradient':
            return self._colorize_gradient_line(text)
        red, green, blue = self._SOFT_WHITE_RGB
        return f'\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m'

    def _colorize_title(self, text: str) -> str:
        """为标题应用渐变强调色。"""
        return self._colorize_gradient_line(text)

    def _colorize_gradient_line(self, text: str) -> str:
        """为一整行文本应用渐变色。"""
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

    def _interpolate_gradient(self, position: float) -> tuple[int, int, int]:
        """在渐变锚点之间线性插值。"""
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

    def _format_duration(self, seconds: float) -> str:
        """把秒数格式化为紧凑的人类可读文本。"""
        total_seconds = max(int(round(seconds)), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f'{hours}h {minutes:02d}m {secs:02d}s'
        if minutes:
            return f'{minutes}m {secs:02d}s'
        return f'{secs}s'

    def _write_blank_lines(self, stream: TextIO, count: int) -> None:
        """输出空行。"""
        for _ in range(count):
            stream.write('\n')

    def _write_line(self, stream: TextIO, text: str) -> None:
        """输出单行。"""
        stream.write(f'{text}\n')