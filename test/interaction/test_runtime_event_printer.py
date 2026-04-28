"""RuntimeEventPrinter 渲染测试。"""

from __future__ import annotations

import io
import time
import unittest

from interaction.runtime_event_printer import RuntimeEventPrinter


class _TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class RuntimeEventPrinterTests(unittest.TestCase):
    def test_non_tty_stream_falls_back_to_stable_log_lines(self) -> None:
        stream = io.StringIO()
        printer = RuntimeEventPrinter(stream=stream)

        printer.emit({'type': 'model_start', 'turn': 1})
        printer.emit({'type': 'model_turn', 'turn': 1, 'finish_reason': 'stop', 'tool_calls': 0})
        printer.flush()

        output = stream.getvalue()
        self.assertIn('[progress] turn 1 requesting model', output)
        self.assertIn('[progress] turn 1 model finished: finish_reason=stop tool_calls=0', output)
        self.assertNotIn('\r', output)

    def test_tty_stream_renders_status_line_and_spinner(self) -> None:
        stream = _TtyStringIO()
        printer = RuntimeEventPrinter(stream=stream)

        printer.emit({'type': 'model_start', 'turn': 1})
        time.sleep(0.15)
        printer.emit({'type': 'model_turn', 'turn': 1, 'finish_reason': 'stop', 'tool_calls': 0})
        printer.flush()

        output = stream.getvalue()
        self.assertIn('\r[progress]', output)
        self.assertIn('requesting model', output)
        self.assertIn('[progress] turn 1 model finished: finish_reason=stop tool_calls=0', output)


if __name__ == '__main__':
    unittest.main()