"""启动渲染器测试。"""

from __future__ import annotations

import io
import unittest

from interaction.environment_summary import EnvironmentLoadSummary
from interaction.startup_render import StartupRenderer


class StartupRendererTests(unittest.TestCase):
    def test_render_wraps_title_and_subtitle_in_single_rounded_box(self) -> None:
        stream = io.StringIO()
        renderer = StartupRenderer(
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

    def test_render_appends_environment_summary_below_box(self) -> None:
        stream = io.StringIO()
        renderer = StartupRenderer(
            lines=('AB',),
            subtitle='EF',
            top_padding=0,
            gap_before_subtitle=0,
            bottom_padding=0,
        )

        renderer.render(
            stream=stream,
            environment_summary=EnvironmentLoadSummary(
                mcp_servers=1,
                plugins=2,
                hook_policies=1,
                search_providers=1,
            ),
        )

        rendered_lines = stream.getvalue().splitlines()

        self.assertEqual(rendered_lines[-2], '')
        self.assertEqual(
            rendered_lines[-1],
            'Environment loaded: 1 MCP server, 2 plugins, 1 hook policy, 1 search provider',
        )


if __name__ == '__main__':
    unittest.main()