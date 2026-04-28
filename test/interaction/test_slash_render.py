"""Slash 面板渲染测试。"""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from interaction.slash_render import SlashCommandRenderer


class _TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class SlashCommandRendererTests(unittest.TestCase):
    def test_render_status_wraps_body_in_panel_and_strips_legacy_heading(self) -> None:
        stream = io.StringIO()
        renderer = SlashCommandRenderer()

        renderer.render(
            command_name='status',
            output=(
                'Session Status\n'
                '==============\n'
                'Session id: session-001\n'
                'Model: demo-model'
            ),
            stream=stream,
        )

        rendered_lines = stream.getvalue().splitlines()
        self.assertTrue(rendered_lines[0].startswith('╭'))
        self.assertTrue(rendered_lines[-1].startswith('╰'))
        self.assertIn('Session Status', stream.getvalue())
        self.assertNotIn('==============', stream.getvalue())
        self.assertIn('Session id: session-001', stream.getvalue())
        self.assertIn('Model: demo-model', stream.getvalue())

    def test_render_clear_uses_compact_layout(self) -> None:
        stream = io.StringIO()
        renderer = SlashCommandRenderer()

        renderer.render(
            command_name='clear',
            output=(
                'Cleared in-memory session context.\n'
                'Previous session id: old-session\n'
                'Cleared session id: cleared-001'
            ),
            stream=stream,
        )

        body_lines = [line[2:-2].rstrip() for line in stream.getvalue().splitlines()[1:-1]]
        self.assertEqual(body_lines[0], 'Session Cleared')
        self.assertEqual(body_lines[1], 'Cleared in-memory session context.')
        self.assertNotEqual(body_lines[1], '')

    def test_render_colorizes_title_when_stream_supports_ansi(self) -> None:
        stream = _TtyStringIO()
        renderer = SlashCommandRenderer()

        with patch.dict(os.environ, {'WT_SESSION': '1'}, clear=False):
            renderer.render(command_name='status', output='Session Status\n==============\nSession id: x', stream=stream)

        output = stream.getvalue()
        self.assertIn('\x1b[38;2;75;123;255mS', output)

    def test_render_tools_keeps_cjk_lines_display_aligned(self) -> None:
        renderer = SlashCommandRenderer()

        framed_lines = renderer._build_framed_lines(
            ('list_dir - 列出工作区目录下的文件和子目录。',),
            use_ansi=False,
        )

        display_widths = [renderer._display_width(line) for line in framed_lines]
        self.assertTrue(all(width == display_widths[0] for width in display_widths))

    def test_render_tools_wraps_long_lines_to_terminal_width(self) -> None:
        stream = io.StringIO()
        renderer = SlashCommandRenderer()
        output = (
            'Registered Tools\n'
            '================\n'
            'read_text_file - Read the complete contents of a file from the file system as text. '
            'Handles various text encodings and provides detailed error messages if the file cannot be read.'
        )

        with patch('interaction.slash_render.shutil.get_terminal_size', return_value=os.terminal_size((80, 24))):
            renderer.render(command_name='tools', output=output, stream=stream)

        rendered_lines = stream.getvalue().splitlines()
        display_widths = [renderer._display_width(line) for line in rendered_lines]
        self.assertTrue(all(width == display_widths[0] for width in display_widths))
        self.assertLessEqual(display_widths[0], 80)
        body_lines = [line[2:-2].rstrip() for line in rendered_lines[1:-1]]
        self.assertIn('read_text_file - Read the complete contents of a file from the file system', body_lines)
        self.assertIn('                 as text. Handles various text encodings and provides', body_lines)


if __name__ == '__main__':
    unittest.main()