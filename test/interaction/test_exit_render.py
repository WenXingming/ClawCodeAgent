"""结束渲染器测试。"""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from interaction.interaction_gateway import ExitRenderer, SessionSummary


class _TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class ExitRendererTests(unittest.TestCase):
    def _extract_box_body(self, rendered_lines: list[str]) -> list[str]:
        self.assertGreaterEqual(len(rendered_lines), 2)

        top_border = rendered_lines[0]
        bottom_border = rendered_lines[-1]
        self.assertEqual(len(top_border), len(bottom_border))
        self.assertTrue(top_border.startswith('╭'))
        self.assertTrue(top_border.endswith('╮'))
        self.assertTrue(bottom_border.startswith('╰'))
        self.assertTrue(bottom_border.endswith('╯'))

        body_lines = rendered_lines[1:-1]
        for line in body_lines:
            self.assertEqual(len(line), len(top_border))
            self.assertTrue(line.startswith('│ '))
            self.assertTrue(line.endswith(' │'))

        return [line[2:-2].rstrip() for line in body_lines]

    def test_render_wraps_summary_in_single_rounded_box(self) -> None:
        stream = io.StringIO()
        renderer = ExitRenderer(top_padding=0)
        summary = SessionSummary(
            session_id='session-001',
            tool_calls=3,
            tool_successes=2,
            tool_failures=1,
            wall_time_seconds=138,
        )

        renderer.render(summary, stream=stream)

        body = self._extract_box_body(stream.getvalue().splitlines())

        self.assertEqual(
            [body[0], body[1], body[2], body[6], body[7], body[9]],
            [
                'Agent powering down. Goodbye!',
                '',
                'Interaction Summary',
                '',
                'Performance',
                '',
            ],
        )
        self.assertRegex(body[3], r'^Session ID:\s+session-001$')
        self.assertRegex(body[4], r'^Tool Calls:\s+3 \(✓ 2 ✗ 1\)$')
        self.assertRegex(body[5], r'^Success Rate:\s+66\.7%$')
        self.assertRegex(body[8], r'^Wall Time:\s+2m 18s$')
        self.assertRegex(body[10], r'^To resume this session:\s+agent-resume session-001$')

    def test_render_uses_soft_white_frame_on_ansi_stream(self) -> None:
        stream = _TtyStringIO()
        renderer = ExitRenderer(top_padding=0)
        summary = SessionSummary(session_id='session-001', wall_time_seconds=1)

        with patch.dict(os.environ, {'WT_SESSION': '1'}, clear=False):
            renderer.render(summary, stream=stream)

        output = stream.getvalue()
        self.assertIn('\x1b[38;2;228;232;240m╭', output)
        self.assertIn('\x1b[38;2;75;123;255mA', output)

    def test_render_supports_gradient_frame_style(self) -> None:
        stream = _TtyStringIO()
        renderer = ExitRenderer(top_padding=0, frame_style='gradient')
        summary = SessionSummary(session_id='session-001', wall_time_seconds=1)

        with patch.dict(os.environ, {'WT_SESSION': '1'}, clear=False):
            renderer.render(summary, stream=stream)

        first_line = stream.getvalue().splitlines()[0]
        self.assertNotIn('\x1b[38;2;228;232;240m╭', first_line)
        self.assertGreater(first_line.count('\x1b[38;2;'), 3)


if __name__ == '__main__':
    unittest.main()