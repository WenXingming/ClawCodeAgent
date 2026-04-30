"""ISSUE-004 基础工具集与执行上下文测试。

本文件覆盖三类目标：
1) 四个基础工具的正常行为。
2) 路径越界与写权限拒绝等安全行为。
3) 结构化错误与输出截断行为。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core_contracts.config import ToolPermissionPolicy
from core_contracts.config import ExecutionPolicy, WorkspaceScope
from tools.tools_gateway import ToolsGateway


class LocalToolsTests(unittest.TestCase):
    """验证 ISSUE-004 工具层最小闭环。"""

    def setUp(self) -> None:
        self.tool_gateway = ToolsGateway()

    def _build_context(
        self,
        workspace: Path,
        *,
        allow_file_write: bool,
        max_output_chars: int = 12000,
    ):
        """构造工具上下文。"""
        workspace_scope = WorkspaceScope(cwd=workspace)
        execution_policy = ExecutionPolicy(max_output_chars=max_output_chars)
        permissions = ToolPermissionPolicy(allow_file_write=allow_file_write)
        registry = self.tool_gateway.default_registry()
        context = self.tool_gateway.build_context(
            workspace_scope,
            execution_policy,
            permissions,
            tool_registry=registry,
        )
        return registry, context

    def test_registry_contains_four_base_tools(self) -> None:
        registry = self.tool_gateway.default_registry()
        self.assertIn('list_dir', registry)
        self.assertIn('read_file', registry)
        self.assertIn('write_file', registry)
        self.assertIn('edit_file', registry)

    def test_list_dir_returns_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'src').mkdir()
            (workspace / 'a.txt').write_text('hello', encoding='utf-8')

            registry, context = self._build_context(workspace, allow_file_write=False)
            result = self.tool_gateway.execute(registry, 'list_dir', {'path': '.'}, context)

        self.assertTrue(result.ok)
        self.assertIn('src/', result.content)
        self.assertIn('a.txt', result.content)
        self.assertEqual(result.metadata.get('action'), 'list_dir')

    def test_read_file_with_line_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'demo.txt').write_text('line1\nline2\nline3\n', encoding='utf-8')

            registry, context = self._build_context(workspace, allow_file_write=False)
            result = self.tool_gateway.execute(
                registry,
                'read_file',
                {
                    'path': 'demo.txt',
                    'start_line': 2,
                    'end_line': 3,
                },
                context,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.content, 'line2\nline3\n')
        self.assertEqual(result.metadata.get('action'), 'read_file')

    def test_read_file_truncates_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'long.txt').write_text('x' * 200, encoding='utf-8')

            registry, context = self._build_context(
                workspace,
                allow_file_write=False,
                max_output_chars=40,
            )
            result = self.tool_gateway.execute(registry, 'read_file', {'path': 'long.txt'}, context)

        self.assertTrue(result.ok)
        self.assertIn('output truncated', result.content)
        self.assertTrue(result.metadata.get('truncated_by_output_limit'))

    def test_write_file_requires_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            registry, context = self._build_context(workspace, allow_file_write=False)
            result = self.tool_gateway.execute(
                registry,
                'write_file',
                {'path': 'a.txt', 'content': 'hello'},
                context,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'permission_denied')

    def test_write_file_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            registry, context = self._build_context(workspace, allow_file_write=True)
            result = self.tool_gateway.execute(
                registry,
                'write_file',
                {'path': 'nested/a.txt', 'content': 'hello'},
                context,
            )
            target = workspace / 'nested' / 'a.txt'
            self.assertTrue(result.ok)
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding='utf-8'), 'hello')
            self.assertEqual(result.metadata.get('action'), 'write_file')

    def test_edit_file_replaces_first_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            target = workspace / 'demo.txt'
            target.write_text('foo foo foo', encoding='utf-8')

            registry, context = self._build_context(workspace, allow_file_write=True)
            result = self.tool_gateway.execute(
                registry,
                'edit_file',
                {'path': 'demo.txt', 'old_text': 'foo', 'new_text': 'bar'},
                context,
            )
            updated = target.read_text(encoding='utf-8')

        self.assertTrue(result.ok)
        self.assertEqual(updated, 'bar foo foo')
        self.assertEqual(result.metadata.get('replaced_count'), 1)

    def test_edit_file_replace_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            target = workspace / 'demo.txt'
            target.write_text('foo foo foo', encoding='utf-8')

            registry, context = self._build_context(workspace, allow_file_write=True)
            result = self.tool_gateway.execute(
                registry,
                'edit_file',
                {
                    'path': 'demo.txt',
                    'old_text': 'foo',
                    'new_text': 'bar',
                    'replace_all': True,
                },
                context,
            )
            updated = target.read_text(encoding='utf-8')

        self.assertTrue(result.ok)
        self.assertEqual(updated, 'bar bar bar')
        self.assertEqual(result.metadata.get('replaced_count'), 3)

    def test_edit_file_missing_text_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            target = workspace / 'demo.txt'
            target.write_text('hello', encoding='utf-8')

            registry, context = self._build_context(workspace, allow_file_write=True)
            result = self.tool_gateway.execute(
                registry,
                'edit_file',
                {'path': 'demo.txt', 'old_text': 'missing', 'new_text': 'x'},
                context,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'tool_execution_error')

    def test_read_file_blocks_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            outside = workspace.parent / 'outside.txt'
            outside.write_text('sensitive', encoding='utf-8')

            registry, context = self._build_context(workspace, allow_file_write=False)
            result = self.tool_gateway.execute(
                registry,
                'read_file',
                {'path': str(outside)},
                context,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'tool_execution_error')
        self.assertIn('escapes workspace root', result.content)

    def test_unknown_tool_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            registry, context = self._build_context(workspace, allow_file_write=False)
            result = self.tool_gateway.execute(registry, 'unknown_tool', {}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'unknown_tool')


if __name__ == '__main__':
    unittest.main()

