"""启动 Banner 渲染器测试。"""

from __future__ import annotations

import io
import unittest

from interface.startup_banner import StartupBannerRenderer


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

        self.assertEqual(
            stream.getvalue().splitlines(),
            [
                '╭──────╮',
                '│  AB  │',
                '│  CD  │',
                '│      │',
                '│  EF  │',
                '│  GH  │',
                '╰──────╯',
            ],
        )


if __name__ == '__main__':
    unittest.main()