"""ISSUE-014 WorkspaceGateway 插件能力单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_contracts.config import ExecutionPolicy, ToolPermissionPolicy, WorkspaceScope
from core_contracts.tools_contracts import ToolExecutionRequest, build_execution_context
from tools import ToolsGatewayFactory
from tools.local.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutor
from backups.workspace import WorkspaceGateway


class WorkspacePluginGatewayTests(unittest.TestCase):
    """验证网关上的插件注册、摘要与 hook/block 能力。"""

    def setUp(self) -> None:
        self.executor = ToolExecutor()
        self.registry = ToolsGatewayFactory.create_default_registry(ShellSecurityPolicy())

    def _write_manifest(self, workspace: Path, filename: str, payload: dict[str, object]) -> None:
        manifest_dir = workspace / '.claw' / 'plugins'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def _build_context(self, workspace: Path, registry: dict[str, object]):
        return build_execution_context(
            WorkspaceScope(cwd=workspace),
            ExecutionPolicy(),
            ToolPermissionPolicy(
                allow_file_write=False,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            ),
            tool_registry=registry,
        )

    def _execute(self, registry: dict, tool_name: str, arguments: dict, context) -> object:
        request = ToolExecutionRequest(tool_name=tool_name, arguments=arguments, context=context)
        return self.executor.execute(
            tool_registry=registry,
            name=request.tool_name,
            arguments=request.arguments,
            context=request.context,
        )

    def test_prepare_tool_registry_registers_alias_and_virtual_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'README.md').write_text('plugin hello', encoding='utf-8')
            self._write_manifest(
                workspace,
                'demo.json',
                {
                    'name': 'demo-plugin',
                    'summary': 'Expose README alias and workspace banner.',
                    'aliases': [
                        {
                            'name': 'read_readme',
                            'target': 'read_file',
                            'description': 'Read README.md through plugin alias.',
                            'arguments': {'path': 'README.md'},
                        }
                    ],
                    'virtual_tools': [
                        {
                            'name': 'workspace_banner',
                            'description': 'Return a fixed plugin banner.',
                            'content': 'Workspace banner from plugin runtime.',
                        }
                    ],
                },
            )

            gateway = WorkspaceGateway.from_workspace(workspace)
            merged_registry = gateway.prepare_tool_registry(self.registry)
            context = self._build_context(workspace, merged_registry)

            alias_result = self._execute(merged_registry, 'read_readme', {}, context)
            virtual_result = self._execute(merged_registry, 'workspace_banner', {}, context)

        self.assertIn('read_readme', merged_registry)
        self.assertIn('workspace_banner', merged_registry)
        self.assertTrue(alias_result.ok)
        self.assertEqual(alias_result.content, 'plugin hello')
        self.assertEqual(alias_result.metadata.get('plugin_name'), 'demo-plugin')
        self.assertEqual(alias_result.metadata.get('plugin_tool_kind'), 'alias')
        self.assertTrue(virtual_result.ok)
        self.assertEqual(virtual_result.content, 'Workspace banner from plugin runtime.')
        self.assertEqual(virtual_result.metadata.get('plugin_name'), 'demo-plugin')
        self.assertEqual(virtual_result.metadata.get('plugin_tool_kind'), 'virtual')
        self.assertEqual(gateway.plugin_count, 1)

        summary = gateway.render_plugin_summary()
        self.assertIn('demo-plugin', summary)
        self.assertIn('Expose README alias and workspace banner.', summary)
        self.assertIn('read_readme', summary)
        self.assertIn('workspace_banner', summary)

    def test_prepare_tool_registry_skips_conflicting_tool_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'conflict.json',
                {
                    'name': 'conflict-plugin',
                    'summary': 'Attempts to shadow a core tool.',
                    'aliases': [
                        {
                            'name': 'read_file',
                            'target': 'list_dir',
                            'description': 'Should be skipped because read_file already exists.',
                            'arguments': {'path': '.'},
                        }
                    ],
                },
            )

            gateway = WorkspaceGateway.from_workspace(workspace)
            merged_registry = gateway.prepare_tool_registry(self.registry)

        self.assertEqual(merged_registry['read_file'].description, self.registry['read_file'].description)

        summary = gateway.render_plugin_summary()
        self.assertIn('conflict-plugin', summary)
        self.assertIn('read_file', summary)
        self.assertIn('skipped', summary)

    def test_gateway_exposes_plugin_hook_and_block_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'hooks.json',
                {
                    'name': 'hook-plugin',
                    'summary': 'Injects hook messages and blocks shell tools.',
                    'deny_prefixes': ['bash'],
                    'before_hooks': [{'kind': 'message', 'content': 'plugin before'}],
                    'after_hooks': [{'kind': 'message', 'content': 'plugin after'}],
                },
            )

            gateway = WorkspaceGateway.from_workspace(workspace)
            gateway.prepare_tool_registry(self.registry)

        self.assertEqual(gateway.get_before_hooks('bash_exec')[0]['content'], 'plugin before')
        self.assertEqual(gateway.get_after_hooks('bash_exec')[0]['content'], 'plugin after')
        self.assertEqual(gateway.resolve_block('bash_exec')['source'], 'plugin')
        self.assertEqual(gateway.resolve_block('bash_exec')['reason'], 'deny_prefixes')


if __name__ == '__main__':
    unittest.main()
