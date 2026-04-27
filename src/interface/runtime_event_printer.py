"""交互式 CLI 运行事件打印器。"""

from __future__ import annotations

import sys
import threading
import time
from typing import TextIO

from core_contracts.protocol import JSONDict


class RuntimeEventPrinter:
    """把运行期结构化事件渲染为稳定日志或增强型 TTY 状态栏。"""

    _SPINNER_FRAMES = ('|', '/', '-', '\\')
    _SPINNER_INTERVAL_SECONDS = 0.1
    _STATUS_EVENT_TYPES = frozenset(
        {
            'model_start',
            'tool_start',
            'delegate_group_start',
            'delegate_child_start',
        }
    )
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
    )

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout
        self._pending_stream_chunks: dict[tuple[str, str, str], str] = {}
        self._supports_tty = bool(getattr(self._stream, 'isatty', lambda: False)())
        self._status_message = ''
        self._status_lock = threading.Lock()
        self._status_width = 0
        self._spinner_index = 0
        self._spinner_stop = threading.Event()
        self._spinner_thread: threading.Thread | None = None

    def emit(self, event: JSONDict) -> None:
        """消费一个结构化运行事件。"""
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
        """输出尚未形成完整行的 tool_stream 残留片段，并清理状态栏。"""
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

    def _set_status(self, message: str) -> None:
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

    def _clear_status(self) -> None:
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

    def _run_spinner(self) -> None:
        while not self._spinner_stop.wait(self._SPINNER_INTERVAL_SECONDS):
            with self._status_lock:
                if not self._status_message:
                    return
                self._render_status_locked()

    def _render_status_locked(self) -> None:
        if not self._status_message:
            return
        frame = self._SPINNER_FRAMES[self._spinner_index % len(self._SPINNER_FRAMES)]
        self._spinner_index += 1
        line = f'[progress] {frame} {self._status_message}'
        padded = line.ljust(self._status_width or len(line))
        self._stream.write(f'\r{padded}')
        self._stream.flush()
        self._status_width = max(self._status_width, len(line))

    def _clear_status_locked(self) -> None:
        if self._status_width <= 0:
            return
        self._stream.write(f'\r{" " * self._status_width}\r')
        self._stream.flush()

    def _print_message(self, message: str) -> None:
        if not self._supports_tty:
            print(message, file=self._stream, flush=True)
            return

        with self._status_lock:
            status_message = self._status_message
            self._clear_status_locked()
            print(message, file=self._stream, flush=True)
            if status_message:
                self._render_status_locked()

    def _format_event_message(self, event: JSONDict) -> str | None:
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

    @staticmethod
    def _format_tool_stream_line(
        *,
        tool_name: str,
        stream_name: str,
        tool_call_id: str,
        content: str,
    ) -> str:
        normalized = content.rstrip('\r\n')
        return f'[progress][{tool_name}][{stream_name}][{tool_call_id}] {normalized}'