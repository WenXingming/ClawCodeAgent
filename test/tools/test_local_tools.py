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
from unittest.mock import MagicMock

from core_contracts.config import ExecutionPolicy, WorkspaceScope
from core_contracts.tools_contracts import ToolExecutionContext, ToolExecutionRequest, ToolPermissionPolicy, ToolRegistry
from tools import ToolsGateway, ToolsGatewayFactory
from tools.local.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutor
from tools.mcp_adapter import McpOperationsAdapter
from tools.registry_builder import DynamicRegistryBuilder


class LocalToolsTests(unittest.TestCase):
    """验证 ISSUE-004 工具层最小闭环。"""

    def setUp(self) -> None:
        self.registry = ToolsGatewayFactory.create_default_registry(ShellSecurityPolicy())
        self.gateway = ToolsGateway(
            local_executor=ToolExecutor(),
            registry_builder=MagicMock(spec=DynamicRegistryBuilder),
            mcp_adapter=MagicMock(spec=McpOperationsAdapter),
            tool_registry=self.registry,
        )

    def _build_context(
        self,
        workspace: Path,
        *,
        allow_file_write: bool,
        max_output_chars: int = 12000,
    ):
        """构造工具上下文。"""
        return ToolExecutionContext.build(
            WorkspaceScope(cwd=workspace),
            ExecutionPolicy(max_output_chars=max_output_chars),
            ToolPermissionPolicy(allow_file_write=allow_file_write),
            tool_registry=self.registry,
        )

    def _execute(self, tool_name: str, arguments: dict, context) -> object:
        """通过网关执行一次工具调用。"""
        request = ToolExecutionRequest(tool_name=tool_name, arguments=arguments, context=context)
        return self.gateway.execute_tool(request)

    def test_registry_contains_four_base_tools(self) -> None:
        self.assertIn('list_dir', self.registry)
        self.assertIn('read_file', self.registry)
        self.assertIn('write_file', self.registry)
        self.assertIn('edit_file', self.registry)

    def test_list_dir_returns_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'src').mkdir()
            (workspace / 'a.txt').write_text('hello', encoding='utf-8')
            context = self._build_context(workspace, allow_file_write=False)
            result = self._execute('list_dir', {'path': '.'}, context)

        self.assertTrue(result.ok)
        self.assertIn('src/', result.content)
        self.assertIn('a.txt', result.content)
        self.assertEqual(result.metadata.get('action'), 'list_dir')

    def test_read_file_with_line_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'demo.txt').write_text('line1\nline2\nline3\n', encoding='utf-8')
            context = self._build_context(workspace, allow_file_write=False)
            result = self._execute('read_file', {'path': 'demo.txt', 'start_line': 2, 'end_line': 3}, context)

        self.assertTrue(result.ok)
        self.assertEqual(result.content, 'line2\nline3\n')
        self.assertEqual(result.metadata.get('action'), 'read_file')

    def test_read_file_truncates_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'long.txt').write_text('x' * 200, encoding='utf-8')
            context = self._build_context(workspace, allow_file_write=False, max_output_chars=40)
            result = self._execute('read_file', {'path': 'long.txt'}, context)

        self.assertTrue(result.ok)
        self.assertIn('output truncated', result.content)
        self.assertTrue(result.metadata.get('truncated_by_output_limit'))

    def test_write_file_requires_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(workspace, allow_file_write=False)
            result = self._execute('write_file', {'path': 'a.txt', 'content': 'hello'}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'permission_denied')

    def test_write_file_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(workspace, allow_file_write=True)
            result = self._execute('write_file', {'path': 'nested/a.txt', 'content': 'hello'}, context)
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
            context = self._build_context(workspace, allow_file_write=True)
            result = self._execute('edit_file', {'path': 'demo.txt', 'old_text': 'foo', 'new_text': 'bar'}, context)
            updated = target.read_text(encoding='utf-8')

        self.assertTrue(result.ok)
        self.assertEqual(updated, 'bar foo foo')
        self.assertEqual(result.metadata.get('replaced_count'), 1)

    def test_edit_file_replace_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            target = workspace / 'demo.txt'
            target.write_text('foo foo foo', encoding='utf-8')
            context = self._build_context(workspace, allow_file_write=True)
            result = self._execute(
                'edit_file',
                {'path': 'demo.txt', 'old_text': 'foo', 'new_text': 'bar', 'replace_all': True},
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
            context = self._build_context(workspace, allow_file_write=True)
            result = self._execute('edit_file', {'path': 'demo.txt', 'old_text': 'missing', 'new_text': 'x'}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'tool_execution_error')

    def test_read_file_blocks_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            outside = workspace.parent / 'outside.txt'
            outside.write_text('sensitive', encoding='utf-8')
            context = self._build_context(workspace, allow_file_write=False)
            result = self._execute('read_file', {'path': str(outside)}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'tool_execution_error')
        self.assertIn('escapes workspace root', result.content)

    def test_unknown_tool_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(workspace, allow_file_write=False)
            result = self._execute('unknown_tool', {}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'unknown_tool')


if __name__ == '__main__':
    unittest.main()
