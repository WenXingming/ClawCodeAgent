"""ISSUE-015 Hook Policy Runtime 单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.registry import LocalTool
from tools.tool_gateway import ToolGateway
from workspace import PolicyCatalog


class PolicyCatalogTests(unittest.TestCase):
    """验证 policy manifest 发现、合并与工具过滤。"""

    def setUp(self) -> None:
        self.tool_gateway = ToolGateway()

    def _write_manifest(self, workspace: Path, filename: str, payload: dict[str, object]) -> None:
        manifest_dir = workspace / '.claw' / 'policies'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def test_from_workspace_merges_trusted_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                '00-base.json',
                {
                    'name': 'base-policy',
                    'trusted': True,
                    'deny_tools': ['read_file'],
                    'deny_prefixes': ['workspace_'],
                    'safe_env': {'POLICY_MODE': 'base', 'SAFE_TOKEN': 'alpha'},
                    'budget_overrides': {'max_model_calls': 2},
                    'before_hooks': [{'kind': 'message', 'content': 'before base'}],
                },
            )
            self._write_manifest(
                workspace,
                '10-override.json',
                {
                    'name': 'override-policy',
                    'trusted': True,
                    'deny_tools': ['edit_file'],
                    'safe_env': {'POLICY_MODE': 'override'},
                    'budget_overrides': {'max_tool_calls': 1},
                    'after_hooks': [{'kind': 'message', 'content': 'after override'}],
                },
            )
            self._write_manifest(
                workspace,
                '20-untrusted.json',
                {
                    'name': 'untrusted-policy',
                    'trusted': False,
                    'deny_tools': ['list_dir'],
                    'safe_env': {'UNSAFE_FLAG': 'ignored'},
                },
            )

            runtime = PolicyCatalog.from_workspace(workspace)

        self.assertEqual([item.name for item in runtime.manifests], ['base-policy', 'override-policy'])
        self.assertEqual(runtime.deny_tools, ('read_file', 'edit_file'))
        self.assertEqual(runtime.deny_prefixes, ('workspace_',))
        self.assertEqual(runtime.safe_env, {'POLICY_MODE': 'override', 'SAFE_TOKEN': 'alpha'})
        self.assertEqual(runtime.budget_overrides.max_model_calls, 2)
        self.assertEqual(runtime.budget_overrides.max_tool_calls, 1)
        self.assertEqual(runtime.before_hooks, ({'kind': 'message', 'content': 'before base'},))
        self.assertEqual(runtime.after_hooks, ({'kind': 'message', 'content': 'after override'},))
        self.assertEqual(len(runtime.skipped_manifests), 1)
        self.assertEqual(runtime.skipped_manifests[0].name, 'untrusted-policy')

    def test_filter_tool_registry_applies_deny_tools_and_prefixes(self) -> None:
        registry = self.tool_gateway.default_registry()
        registry['workspace_banner'] = LocalTool(
            name='workspace_banner',
            description='plugin virtual tool',
            parameters={'type': 'object', 'properties': {}},
            handler=lambda arguments, context: 'ok',
        )

        runtime = PolicyCatalog(
            deny_tools=('read_file',),
            deny_prefixes=('workspace_',),
        )
        filtered = runtime.filter_tool_registry(registry)

        self.assertNotIn('read_file', filtered)
        self.assertNotIn('workspace_banner', filtered)
        self.assertIn('list_dir', filtered)

    def test_runtime_exposes_hook_and_block_helpers(self) -> None:
        runtime = PolicyCatalog(
            manifests=(),
            deny_tools=('read_file',),
            deny_prefixes=('workspace_',),
        )

        self.assertEqual(runtime.resolve_block('read_file')['source'], 'policy')
        self.assertEqual(runtime.resolve_block('workspace_banner')['reason'], 'deny_prefixes')

        runtime = PolicyCatalog(
            manifests=(),
            before_hooks=({'kind': 'message', 'content': 'policy before'},),
            after_hooks=({'kind': 'message', 'content': 'policy after'},),
        )

        self.assertEqual(runtime.get_before_hooks('list_dir')[0]['content'], 'policy before')
        self.assertEqual(runtime.get_after_hooks('list_dir')[0]['content'], 'policy after')


if __name__ == '__main__':
    unittest.main()
