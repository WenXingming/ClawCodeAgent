"""CLI 会话结束提示框渲染模块。

本模块负责在交互式 CLI 会话结束时输出统一的总结提示框，覆盖三类职责：
1. 累计一次会话交互期间的工具调用与耗时统计；
2. 将累计状态投影为稳定的会话总结对象；
3. 按终端能力渲染带边框、可选渐变标题的结束提示框。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, TextIO

from core_contracts.run_result import AgentRunResult


@dataclass
class SessionInteractionSummary:
    """表示一次 CLI 交互结束时的只读汇总快照。

    外部通常不直接手工构造该对象，而是通过 SessionInteractionTracker.to_summary()
    从累计态生成，再传给 SessionExitSummaryRenderer.render() 输出到终端。
    """

    session_id: str | None = None  # str | None: 最后一个已知会话 ID；无会话时为 None。
    tool_calls: int = 0  # int: 本次交互期间累计发生的工具调用总数。
    tool_successes: int = 0  # int: 工具调用成功次数。
    tool_failures: int = 0  # int: 工具调用失败次数。
    wall_time_seconds: float = 0.0  # float: 本次交互累计墙钟耗时，单位为秒。

    @property
    def success_rate(self) -> float:
        """返回工具调用成功率。

        Args:
            None: 该属性直接基于当前对象字段计算。
        Returns:
            float: 成功调用数除以总调用数后的比例；当总调用数为 0 时返回 0.0。
        """
        if self.tool_calls <= 0:
            return 0.0
        return self.tool_successes / self.tool_calls


@dataclass
class SessionInteractionTracker:
    """维护一次 CLI 交互生命周期内的可变累计统计。

    工作流为：先通过 start() 创建追踪器；在每轮执行后调用 observe_run_result()
    吸收一轮结果中的会话与工具统计；最后通过 to_summary() 产出可渲染的总结对象。
    """

    session_id: str | None = None  # str | None: 当前已知的活动会话 ID。
    started_time: float = 0.0  # float: 统计开始时的 perf_counter 基准值。
    tool_calls: int = 0  # int: 已累计的工具调用总次数。
    tool_successes: int = 0  # int: 已累计的成功工具调用次数。
    tool_failures: int = 0  # int: 已累计的失败工具调用次数。

    @classmethod
    def start(cls, session_id: str | None = None) -> 'SessionInteractionTracker':
        """创建新的交互汇总状态。

        Args:
            session_id (str | None): 初始会话 ID；尚未建立会话时可为 None。
        Returns:
            SessionInteractionTracker: 已记录启动时间的新追踪器实例。
        """
        return cls(session_id=session_id, started_time=perf_counter())

    def observe_run_result(
        self,
        result: AgentRunResult,
        *,
        current_session_id: str | None,
    ) -> None:
        """吸收单轮执行结果中的增量统计。

        Args:
            result (AgentRunResult): 当前轮执行结果，包含会话 ID 与结构化事件列表。
            current_session_id (str | None): 当前已知的活动会话 ID，用于在结果未显式返回 session_id 时回退。
        Returns:
            None: 该方法只更新追踪器内部状态。
        """
        self.update_session_id(result.session_id or current_session_id)
        for event in result.events:
            if event.get('type') != 'tool_result':
                continue
            self.observe_tool_result(ok=bool(event.get('ok')))

    def observe_tool_result(self, *, ok: bool) -> None:
        """累计一次工具结果。

        Args:
            ok (bool): 当前工具调用是否成功。
        Returns:
            None: 该方法只更新内部累计计数。
        """
        self.tool_calls += 1
        if ok:
            self.tool_successes += 1
            return
        self.tool_failures += 1

    def update_session_id(self, session_id: str | None) -> None:
        """刷新最后一个已知的活动 session id。

        Args:
            session_id (str | None): 新观察到的会话 ID；为空时忽略。
        Returns:
            None: 该方法只在存在有效会话 ID 时更新内部状态。
        """
        if session_id:
            self.session_id = session_id

    def to_summary(self) -> SessionInteractionSummary:
        """将累计状态投影为可渲染的总结对象。

        Args:
            None: 该方法直接读取当前追踪器状态。
        Returns:
            SessionInteractionSummary: 包含会话 ID、工具统计与耗时的总结对象。
        """
        return SessionInteractionSummary(
            session_id=self.session_id,
            tool_calls=self.tool_calls,
            tool_successes=self.tool_successes,
            tool_failures=self.tool_failures,
            wall_time_seconds=max(perf_counter() - self.started_time, 0.0),
        )


class SessionExitSummaryRenderer:
    """渲染 CLI 会话结束提示框。

    外部通过 render() 传入 SessionInteractionSummary，本类会按以下顺序工作：
    1. 判断目标流是否支持 ANSI；
    2. 组装会话总结正文；
    3. 为正文添加统一边框与可选颜色；
    4. 输出上下外边距与整块提示框。

    其中颜色常量属于类级共享样式，而边框内边距属于单个渲染器实例的布局状态。
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
        """初始化结束提示框渲染器。

        Args:
            title (str): 提示框第一行标题文本。
            frame_style (Literal['soft_white', 'gradient']): 边框配色方案；soft_white 使用纯浅白，gradient 使用渐变。
            top_padding (int): 提示框上方外边距行数，小于 0 时按 0 处理。
            bottom_padding (int): 提示框下方外边距行数，小于 0 时按 0 处理。
        Returns:
            None: 构造函数只建立渲染器实例状态。
        """
        self._title = title  # str: 提示框标题行文本。
        self._frame_style = frame_style  # Literal['soft_white', 'gradient']: 当前实例使用的边框着色模式。
        self._frame_horizontal_padding = 1  # int: 边框左右内边距，单位为字符。
        self._frame_vertical_padding = 0  # int: 边框上下内边距，单位为字符行数。
        self._top_padding = max(top_padding, 0)  # int: 提示框上方外边距行数。
        self._bottom_padding = max(bottom_padding, 0)  # int: 提示框下方外边距行数。

    def render(self, summary: SessionInteractionSummary, stream: TextIO | None = None) -> None:
        """将结束总结输出到目标流。

        Args:
            summary (SessionInteractionSummary): 待渲染的会话总结对象。
            stream (TextIO | None): 目标文本流；为 None 时默认写入 sys.stdout。
        Returns:
            None: 该方法只负责把提示框写入目标流。
        """
        target = stream or sys.stdout
        use_ansi = self._stream_supports_ansi(target)
        content_lines = self._build_content_lines(summary)
        framed_lines = self._build_framed_lines(content_lines, use_ansi)

        self._write_blank_lines(target, self._top_padding)
        for line in framed_lines:
            self._write_line(target, line)
        self._write_blank_lines(target, self._bottom_padding)

    def _stream_supports_ansi(self, stream: TextIO) -> bool:
        """检测目标流是否支持 ANSI。

        Args:
            stream (TextIO): 待检测的输出流。
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

    def _build_content_lines(self, summary: SessionInteractionSummary) -> tuple[str, ...]:
        """构建提示框正文。

        Args:
            summary (SessionInteractionSummary): 当前会话的汇总快照。
        Returns:
            tuple[str, ...]: 供边框渲染器消费的正文行元组。
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

    def _build_framed_lines(self, content_lines: tuple[str, ...], use_ansi: bool) -> tuple[str, ...]:
        """构建带圆角边框的提示框。

        Args:
            content_lines (tuple[str, ...]): 未加边框的正文行。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            tuple[str, ...]: 已附加边框与边距的完整输出行。
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
        """在边框内容上下追加空白行。

        Args:
            content_lines (tuple[str, ...]): 原始正文行。
        Returns:
            tuple[str, ...]: 追加上下内边距后的正文行。
        """
        vertical_padding = ('',) * self._frame_vertical_padding
        return (*vertical_padding, *content_lines, *vertical_padding)

    def _frame_border_line(self, inner_width: int, *, top: bool, use_ansi: bool) -> str:
        """生成顶部或底部边框。

        Args:
            inner_width (int): 边框内部宽度，不含左右角字符。
            top (bool): True 生成顶部边框，False 生成底部边框。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 单行边框文本。
        """
        left_corner, right_corner = ('╭', '╮') if top else ('╰', '╯')
        border = f'{left_corner}{"─" * inner_width}{right_corner}'
        return self._colorize_frame(border) if use_ansi else border

    def _colorize_frame(self, text: str) -> str:
        """为边框应用浅白或渐变色。

        Args:
            text (str): 待着色的边框文本。
        Returns:
            str: 着色后的边框文本。
        """
        if self._frame_style == 'gradient':
            return self._colorize_gradient_line(text)
        red, green, blue = self._SOFT_WHITE_RGB
        return f'\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m'

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

    def _interpolate_gradient(self, position: float) -> tuple[int, int, int]:
        """在渐变锚点之间线性插值。

        Args:
            position (float): 归一化位置，范围通常为 0.0 到 1.0。
        Returns:
            tuple[int, int, int]: 插值后的 RGB 颜色三元组。
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

    def _frame_content_line(self, text: str, content_width: int, use_ansi: bool) -> str:
        """生成一行正文。

        Args:
            text (str): 当前正文文本。
            content_width (int): 正文区域的最大宽度。
            use_ansi (bool): 是否启用 ANSI 着色。
        Returns:
            str: 已补齐宽度并加上左右边框的单行文本。
        """
        padded_text = text.ljust(content_width)
        rendered_text = self._colorize_title(padded_text) if use_ansi and text == self._title else padded_text
        left_border = self._colorize_frame('│') if use_ansi else '│'
        right_border = self._colorize_frame('│') if use_ansi else '│'
        horizontal_padding = ' ' * self._frame_horizontal_padding
        return f'{left_border}{horizontal_padding}{rendered_text}{horizontal_padding}{right_border}'

    def _colorize_title(self, text: str) -> str:
        """为标题应用渐变强调色。

        Args:
            text (str): 待着色的标题文本。
        Returns:
            str: 应用渐变后的标题文本。
        """
        return self._colorize_gradient_line(text)

    def _write_blank_lines(self, stream: TextIO, count: int) -> None:
        """输出空行。

        Args:
            stream (TextIO): 目标输出流。
            count (int): 需要输出的空行数量。
        Returns:
            None: 该方法只向流中写入换行符。
        """
        for _ in range(count):
            stream.write('\n')

    def _write_line(self, stream: TextIO, text: str) -> None:
        """输出单行。

        Args:
            stream (TextIO): 目标输出流。
            text (str): 待输出的完整单行文本。
        Returns:
            None: 该方法只负责把文本写入流并补一个换行。
        """
        stream.write(f'{text}\n')