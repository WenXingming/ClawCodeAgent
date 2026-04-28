"""启动 Banner 渲染器测试。"""

from __future__ import annotations

import io
import unittest

from interaction.startup_banner import StartupBannerRenderer


class StartupBannerRendererTests(unittest.TestCase):
    def test_render_wraps_title_and_subtitle_in_single_rounded_box(self) -> None:
        stream = io.StringIO()
        renderer = StartupBannerRenderer(
            lines=('AB', 'CD'),
            subtitle='EF\nGH',
            top_padding=0,
            gap_before_subtitle=1,
            bottom_padding=0,
        )

        renderer.render(stream=stream)

        rendered_lines = stream.getvalue().splitlines()

        self.assertGreaterEqual(len(rendered_lines), 3)
        self.assertTrue(rendered_lines[0].startswith('╭'))
        self.assertTrue(rendered_lines[0].endswith('╮'))
        self.assertTrue(rendered_lines[-1].startswith('╰'))
        self.assertTrue(rendered_lines[-1].endswith('╯'))

        inner_lines = rendered_lines[1:-1]
        self.assertTrue(all(line.startswith('│') and line.endswith('│') for line in inner_lines))

        content_lines = [line[1:-1].strip() for line in inner_lines]
        while content_lines and content_lines[0] == '':
            content_lines.pop(0)
        while content_lines and content_lines[-1] == '':
            content_lines.pop()

        self.assertEqual(content_lines, ['AB', 'CD', '', 'EF', 'GH'])


if __name__ == '__main__':
    unittest.main()