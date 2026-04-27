"""结束提示框渲染器测试。"""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from interface.exit_banner import SessionExitSummary, SessionExitSummaryRenderer


class _TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class SessionExitSummaryRendererTests(unittest.TestCase):
    def test_render_wraps_summary_in_single_rounded_box(self) -> None:
        stream = io.StringIO()
        renderer = SessionExitSummaryRenderer(top_padding=0)
        summary = SessionExitSummary(
            session_id='session-001',
            tool_calls=3,
            tool_successes=2,
            tool_failures=1,
            wall_time_seconds=138,
        )

        renderer.render(summary, stream=stream)

        self.assertEqual(
            stream.getvalue().splitlines(),
            [
                '╭──────────────────────────────────────────────────╮',
                '│ Agent powering down. Goodbye!                    │',
                '│                                                  │',
                '│ Interaction Summary                              │',
                '│ Session ID:   session-001                        │',
                '│ Tool Calls:   3 (✓ 2 ✗ 1)                        │',
                '│ Success Rate: 66.7%                              │',
                '│                                                  │',
                '│ Performance                                      │',
                '│ Wall Time:    2m 18s                             │',
                '│                                                  │',
                '│ To resume this session: agent-resume session-001 │',
                '╰──────────────────────────────────────────────────╯',
            ],
        )

    def test_render_uses_soft_white_frame_on_ansi_stream(self) -> None:
        stream = _TtyStringIO()
        renderer = SessionExitSummaryRenderer(top_padding=0)
        summary = SessionExitSummary(session_id='session-001', wall_time_seconds=1)

        with patch.dict(os.environ, {'WT_SESSION': '1'}, clear=False):
            renderer.render(summary, stream=stream)

        output = stream.getvalue()
        self.assertIn('\x1b[38;2;228;232;240m╭', output)
        self.assertIn('\x1b[38;2;75;123;255mA', output)

    def test_render_supports_gradient_frame_style(self) -> None:
        stream = _TtyStringIO()
        renderer = SessionExitSummaryRenderer(top_padding=0, frame_style='gradient')
        summary = SessionExitSummary(session_id='session-001', wall_time_seconds=1)

        with patch.dict(os.environ, {'WT_SESSION': '1'}, clear=False):
            renderer.render(summary, stream=stream)

        first_line = stream.getvalue().splitlines()[0]
        self.assertNotIn('\x1b[38;2;228;232;240m╭', first_line)
        self.assertGreater(first_line.count('\x1b[38;2;'), 3)


if __name__ == '__main__':
    unittest.main()