"""交互式 CLI 运行事件打印模块。

本模块负责把运行期结构化事件稳定地渲染为两种终端输出形态：
1. 非 TTY 环境下的普通逐行日志；
2. TTY 环境下的动态状态栏加逐行进度日志。

其中 tool_stream 事件需要做分块拼接与整行冲刷，其他事件则按事件类型格式化为进度消息。
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TextIO

from core_contracts.protocol import JSONDict


class RuntimeEventPrinter:
    """把运行期结构化事件渲染为稳定日志或增强型 TTY 状态栏。

    外部通常在交互循环开始时创建一个实例，并在每个运行期事件到来时调用 emit()。
    当会话即将结束或一轮执行完成时，调用 flush() 可以把尚未形成完整行的工具输出片段冲刷出来。

    该类内部维护工具流拼接缓存、TTY 状态消息、状态栏宽度以及 spinner 线程生命周期。
    """

    _SPINNER_FRAMES = ('|', '/', '-', '\\')  # tuple[str, ...]: 状态栏 spinner 的循环帧序列。
    _SPINNER_INTERVAL_SECONDS = 0.1  # float: spinner 刷新间隔，单位为秒。
    _STATUS_EVENT_TYPES = frozenset(
        {
            'model_start',
            'tool_start',
            'delegate_group_start',
            'delegate_child_start',
        }
    )  # frozenset[str]: 需要显示为持续状态栏的事件类型集合。
    _STATUS_CLEARING_EVENT_TYPES = frozenset(
        {
            'model_turn',
            'tool_result',
            'tool_blocked',
            'budget_stop',
            'delegate_child_complete',
            'delegate_child_skipped',
            'delegate_group_complete',
        }
    )  # frozenset[str]: 需要先清空状态栏再打印结果日志的事件类型集合。

    def __init__(self, stream: TextIO | None = None) -> None:
        """初始化运行事件打印器。

        Args:
            stream (TextIO | None): 目标输出流；为 None 时默认写入 sys.stdout。
        Returns:
            None: 构造函数只初始化内部渲染状态。
        """
        self._stream = stream or sys.stdout  # TextIO: 实际输出目标流。
        self._pending_stream_chunks: dict[tuple[str, str, str], str] = {}
        # dict[tuple[str, str, str], str]: 按工具名、流名、调用 ID 聚合的未完成输出片段缓存。

        self._supports_tty = bool(getattr(self._stream, 'isatty', lambda: False)())
        # bool: 当前输出流是否支持动态状态栏刷新。

        self._status_message = ''
        # str: 当前 TTY 状态栏展示的主消息；为空时表示无需显示状态栏。

        self._status_lock = threading.Lock()
        # threading.Lock: 保护状态栏消息、宽度与 spinner 状态的并发访问。

        self._status_width = 0
        # int: 最近一次状态栏占用的可见宽度，用于清屏补空格。

        self._spinner_index = 0
        # int: spinner 当前帧序号。

        self._spinner_stop = threading.Event()
        # threading.Event: 控制 spinner 线程停止的信号。

        self._spinner_thread: threading.Thread | None = None
        # threading.Thread | None: 后台 spinner 线程；无活动线程时为 None。

    def emit(self, event: JSONDict) -> None:
        """消费一个结构化运行事件。

        Args:
            event (JSONDict): 单条结构化事件字典，至少应包含 type 字段。
        Returns:
            None: 该方法只负责按事件类型更新状态栏或输出日志。
        """
        event_type = event.get('type')
        if not isinstance(event_type, str) or not event_type:
            return

        if event_type == 'tool_stream':
            self._emit_tool_stream(event)
            return

        message = self._format_event_message(event)
        if not message:
            return

        if self._supports_tty and event_type in self._STATUS_EVENT_TYPES:
            self._set_status(message)
            return

        if self._supports_tty and event_type in self._STATUS_CLEARING_EVENT_TYPES:
            self._clear_status()
            self._print_message(message)
            return

        self._print_message(message)

    def flush(self) -> None:
        """输出尚未形成完整行的 tool_stream 残留片段，并清理状态栏。

        Args:
            None: 该方法直接消费内部缓存状态。
        Returns:
            None: 该方法只负责冲刷缓存并清空状态栏。
        """
        for key, chunk in list(self._pending_stream_chunks.items()):
            if not chunk:
                continue
            tool_name, stream_name, tool_call_id = key
            self._print_message(
                self._format_tool_stream_line(
                    tool_name=tool_name,
                    stream_name=stream_name,
                    tool_call_id=tool_call_id,
                    content=chunk,
                )
            )
        self._pending_stream_chunks.clear()
        self._clear_status()

    def _emit_tool_stream(self, event: JSONDict) -> None:
        """处理 tool_stream 事件并按完整行输出。

        Args:
            event (JSONDict): tool_stream 事件字典，需包含 tool_name、stream、tool_call_id 与 chunk。
        Returns:
            None: 该方法只更新流片段缓存并在形成完整行时输出。
        """
        tool_name = str(event.get('tool_name') or 'tool')
        stream_name = str(event.get('stream') or 'stdout')
        tool_call_id = str(event.get('tool_call_id') or '?')
        chunk = event.get('chunk')
        if not isinstance(chunk, str) or not chunk:
            return

        key = (tool_name, stream_name, tool_call_id)
        combined = self._pending_stream_chunks.get(key, '') + chunk
        if not combined:
            return

        trailing_fragment = ''
        if not combined.endswith(('\n', '\r')):
            newline_index = max(combined.rfind('\n'), combined.rfind('\r'))
            if newline_index == -1:
                self._pending_stream_chunks[key] = combined
                return
            trailing_fragment = combined[newline_index + 1 :]
            complete_portion = combined[: newline_index + 1]
        else:
            complete_portion = combined

        for line in complete_portion.splitlines():
            if not line:
                continue
            self._print_message(
                self._format_tool_stream_line(
                    tool_name=tool_name,
                    stream_name=stream_name,
                    tool_call_id=tool_call_id,
                    content=line,
                )
            )

        if trailing_fragment:
            self._pending_stream_chunks[key] = trailing_fragment
        else:
            self._pending_stream_chunks.pop(key, None)

    @staticmethod
    def _format_tool_stream_line(
        *,
        tool_name: str,
        stream_name: str,
        tool_call_id: str,
        content: str,
    ) -> str:
        """把工具流输出格式化为统一的进度日志行。

        Args:
            tool_name (str): 工具名称。
            stream_name (str): 工具输出流名称，通常为 stdout 或 stderr。
            tool_call_id (str): 当前工具调用标识。
            content (str): 单行输出内容。
        Returns:
            str: 带统一前缀的单行日志文本。
        """
        normalized = content.rstrip('\r\n')
        return f'[progress][{tool_name}][{stream_name}][{tool_call_id}] {normalized}'

    def _print_message(self, message: str) -> None:
        """打印单条日志消息，并在 TTY 场景下与状态栏共存。

        Args:
            message (str): 待输出的日志文本。
        Returns:
            None: 该方法只负责输出日志并在必要时恢复状态栏。
        """
        if not self._supports_tty:
            print(message, file=self._stream, flush=True)
            return

        with self._status_lock:
            status_message = self._status_message
            self._clear_status_locked()
            print(message, file=self._stream, flush=True)
            if status_message:
                self._render_status_locked()

    def _clear_status_locked(self) -> None:
        """在已持有状态锁时清除当前状态栏显示。

        Args:
            None: 该方法直接消费实例状态。
        Returns:
            None: 该方法只向输出流写入回车与空格完成清屏。
        """
        if self._status_width <= 0:
            return
        self._stream.write(f'\r{" " * self._status_width}\r')
        self._stream.flush()

    def _render_status_locked(self) -> None:
        """在已持有状态锁时渲染当前状态栏。

        Args:
            None: 该方法直接读取实例状态。
        Returns:
            None: 该方法只向输出流刷新一行动态状态栏。
        """
        if not self._status_message:
            return
        frame = self._SPINNER_FRAMES[self._spinner_index % len(self._SPINNER_FRAMES)]
        self._spinner_index += 1
        line = f'[progress] {frame} {self._status_message}'
        padded = line.ljust(self._status_width or len(line))
        self._stream.write(f'\r{padded}')
        self._stream.flush()
        self._status_width = max(self._status_width, len(line))

    def _format_event_message(self, event: JSONDict) -> str | None:
        """把非 tool_stream 事件格式化为稳定的进度消息。

        Args:
            event (JSONDict): 结构化运行事件。
        Returns:
            str | None: 成功映射时返回日志文本；不需要输出时返回 None。
        """
        event_type = str(event.get('type') or '')
        turn = event.get('turn')
        turn_prefix = f'turn {turn} ' if isinstance(turn, int) else ''

        if event_type == 'model_start':
            return f'[progress] {turn_prefix}requesting model'.strip()
        if event_type == 'model_turn':
            finish_reason = event.get('finish_reason') or 'unknown'
            tool_calls = event.get('tool_calls') or 0
            return f'[progress] {turn_prefix}model finished: finish_reason={finish_reason} tool_calls={tool_calls}'.strip()
        if event_type == 'tool_start':
            tool_name = event.get('tool_name') or 'tool'
            return f'[progress] {turn_prefix}starting tool {tool_name}'.strip()
        if event_type == 'tool_blocked':
            tool_name = event.get('tool_name') or 'tool'
            reason = event.get('reason') or 'blocked'
            return f'[progress] {turn_prefix}blocked tool {tool_name}: {reason}'.strip()
        if event_type == 'tool_result':
            tool_name = event.get('tool_name') or 'tool'
            ok = bool(event.get('ok'))
            error_kind = event.get('error_kind')
            suffix = f' error_kind={error_kind}' if isinstance(error_kind, str) and error_kind else ''
            return f'[progress] {turn_prefix}tool {tool_name} finished: ok={ok}{suffix}'.strip()
        if event_type == 'budget_stop':
            reason = event.get('reason') or 'unknown'
            return f'[progress] {turn_prefix}stopped by budget: {reason}'.strip()
        if event_type == 'snip_boundary':
            snipped_count = event.get('snipped_count') or 0
            tokens_removed = event.get('tokens_removed') or 0
            return f'[progress] {turn_prefix}snipped context: snipped_count={snipped_count} tokens_removed={tokens_removed}'.strip()
        if event_type == 'compact_boundary':
            summary_chars = event.get('summary_chars') or 0
            return f'[progress] {turn_prefix}compacted context: summary_chars={summary_chars}'.strip()
        if event_type == 'reactive_compact_retry':
            return f'[progress] {turn_prefix}retrying after reactive compact'.strip()
        if event_type == 'delegate_group_start':
            child_count = event.get('child_count') or 0
            return f'[progress] delegate group started: child_count={child_count}'.strip()
        if event_type == 'delegate_child_start':
            task_id = event.get('task_id') or 'unknown'
            return f'[progress] delegate child started: task_id={task_id}'.strip()
        if event_type == 'delegate_child_complete':
            task_id = event.get('task_id') or 'unknown'
            stop_reason = event.get('stop_reason') or 'unknown'
            return f'[progress] delegate child completed: task_id={task_id} stop_reason={stop_reason}'.strip()
        if event_type == 'delegate_child_skipped':
            task_id = event.get('task_id') or 'unknown'
            reason = event.get('reason') or 'unknown'
            return f'[progress] delegate child skipped: task_id={task_id} reason={reason}'.strip()
        if event_type == 'delegate_group_complete':
            status = event.get('status') or 'unknown'
            return f'[progress] delegate group completed: status={status}'.strip()
        return None

    def _set_status(self, message: str) -> None:
        """设置并显示当前状态栏消息。

        Args:
            message (str): 需要持续展示在状态栏中的消息文本。
        Returns:
            None: 该方法只更新状态栏状态，必要时启动 spinner 线程。
        """
        if not self._supports_tty:
            self._print_message(message)
            return

        with self._status_lock:
            self._status_message = message
            self._spinner_index = 0
            self._render_status_locked()
            if self._spinner_thread is None or not self._spinner_thread.is_alive():
                self._spinner_stop.clear()
                self._spinner_thread = threading.Thread(target=self._run_spinner, daemon=True)
                self._spinner_thread.start()

    def _run_spinner(self) -> None:
        """在后台周期性刷新 TTY 状态栏 spinner。

        Args:
            None: 该方法直接读取实例状态。
        Returns:
            None: 线程退出时不返回任何值。
        """
        while not self._spinner_stop.wait(self._SPINNER_INTERVAL_SECONDS):
            with self._status_lock:
                if not self._status_message:
                    return
                self._render_status_locked()

    def _clear_status(self) -> None:
        """停止 spinner 并清空当前状态栏。

        Args:
            None: 该方法直接消费实例状态。
        Returns:
            None: 该方法只负责停止状态栏刷新并清理显示。
        """
        if not self._supports_tty:
            return

        thread: threading.Thread | None = None
        with self._status_lock:
            self._status_message = ''
            self._spinner_stop.set()
            thread = self._spinner_thread
            self._spinner_thread = None
            self._clear_status_locked()
        if thread is not None and thread.is_alive():
            thread.join(timeout=self._SPINNER_INTERVAL_SECONDS * 2)