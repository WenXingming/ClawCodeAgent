"""ISSUE-014 Plugin Runtime 单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_contracts.config import AgentPermissions, AgentRuntimeConfig
from extensions.plugin_runtime import PluginRuntime
from tools.agent_tools import build_tool_context, default_tool_registry, execute_tool


class PluginRuntimeTests(unittest.TestCase):
    """验证 manifest 发现、alias/virtual 注册与冲突处理。"""

    def _write_manifest(self, workspace: Path, filename: str, payload: dict[str, object]) -> None:
        manifest_dir = workspace / '.claw' / 'plugins'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def _build_context(self, workspace: Path, registry: dict[str, object]):
        config = AgentRuntimeConfig(
            cwd=workspace,
            permissions=AgentPermissions(
                allow_file_write=False,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            ),
        )
        return build_tool_context(config, tool_registry=registry)

    def test_from_workspace_registers_alias_and_virtual_tools(self) -> None:
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

            base_registry = default_tool_registry()
            plugin_runtime = PluginRuntime.from_workspace(workspace, base_registry)
            merged_registry = plugin_runtime.merge_tool_registry(base_registry)
            context = self._build_context(workspace, merged_registry)

            alias_result = execute_tool(merged_registry, 'read_readme', {}, context)
            virtual_result = execute_tool(merged_registry, 'workspace_banner', {}, context)

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

        summary = plugin_runtime.render_summary()
        self.assertIn('demo-plugin', summary)
        self.assertIn('Expose README alias and workspace banner.', summary)
        self.assertIn('read_readme', summary)
        self.assertIn('workspace_banner', summary)

    def test_from_workspace_skips_conflicting_tool_names(self) -> None:
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

            base_registry = default_tool_registry()
            plugin_runtime = PluginRuntime.from_workspace(workspace, base_registry)
            merged_registry = plugin_runtime.merge_tool_registry(base_registry)

        self.assertEqual(merged_registry['read_file'].description, base_registry['read_file'].description)
        self.assertEqual(len(plugin_runtime.conflicts), 1)
        self.assertEqual(plugin_runtime.conflicts[0].tool_name, 'read_file')
        self.assertEqual(plugin_runtime.conflicts[0].plugin_name, 'conflict-plugin')

        summary = plugin_runtime.render_summary()
        self.assertIn('conflict-plugin', summary)
        self.assertIn('read_file', summary)
        self.assertIn('skipped', summary)

    def test_from_workspace_exposes_hook_and_block_helpers(self) -> None:
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

            plugin_runtime = PluginRuntime.from_workspace(workspace, default_tool_registry())

        self.assertEqual(plugin_runtime.get_before_hooks('bash_exec')[0]['content'], 'plugin before')
        self.assertEqual(plugin_runtime.get_after_hooks('bash_exec')[0]['content'], 'plugin after')
        self.assertEqual(plugin_runtime.resolve_block('bash_exec')['source'], 'plugin')
        self.assertEqual(plugin_runtime.resolve_block('bash_exec')['reason'], 'deny_prefixes')


if __name__ == '__main__':
    unittest.main()
