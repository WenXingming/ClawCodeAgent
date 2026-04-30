"""ISSUE-015 WorkspaceGateway 策略能力单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_contracts.config import BudgetConfig
from core_contracts.tools_contracts import ToolDescriptor
from tools.tools_gateway import ToolsGateway
from workspace import WorkspaceGateway


class WorkspacePolicyGatewayTests(unittest.TestCase):
    """验证策略 manifest 发现、合并与工具过滤。"""

    def setUp(self) -> None:
        self.tool_gateway = ToolsGateway()

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

            gateway = WorkspaceGateway.from_workspace(workspace)
            merged = gateway.prepare_tool_registry(self.tool_gateway.default_registry())
            applied_budget = gateway.apply_budget_config(BudgetConfig())

        self.assertNotIn('read_file', merged)
        self.assertNotIn('edit_file', merged)
        self.assertEqual(gateway.safe_env, {'POLICY_MODE': 'override', 'SAFE_TOKEN': 'alpha'})
        self.assertEqual(applied_budget.max_model_calls, 2)
        self.assertEqual(applied_budget.max_tool_calls, 1)
        self.assertEqual(gateway.get_before_hooks('list_dir')[0]['content'], 'before base')
        self.assertEqual(gateway.get_after_hooks('list_dir')[0]['content'], 'after override')
        self.assertEqual(gateway.policy_count, 2)

    def test_filter_tool_registry_applies_deny_tools_and_prefixes(self) -> None:
        registry = self.tool_gateway.default_registry()
        registry['workspace_banner'] = ToolDescriptor(
            name='workspace_banner',
            description='plugin virtual tool',
            parameters={'type': 'object', 'properties': {}},
            handler=lambda arguments, context: 'ok',
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'policy.json',
                {
                    'name': 'base-policy',
                    'trusted': True,
                    'deny_tools': ['read_file'],
                    'deny_prefixes': ['workspace_'],
                },
            )
            gateway = WorkspaceGateway.from_workspace(workspace)
            filtered = gateway.prepare_tool_registry(registry)

        self.assertNotIn('read_file', filtered)
        self.assertNotIn('workspace_banner', filtered)
        self.assertIn('list_dir', filtered)

    def test_gateway_exposes_policy_block_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'policy.json',
                {
                    'name': 'base-policy',
                    'trusted': True,
                    'deny_tools': ['read_file'],
                    'deny_prefixes': ['workspace_'],
                    'before_hooks': [{'kind': 'message', 'content': 'policy before'}],
                    'after_hooks': [{'kind': 'message', 'content': 'policy after'}],
                },
            )
            gateway = WorkspaceGateway.from_workspace(workspace)
            gateway.prepare_tool_registry(self.tool_gateway.default_registry())

        self.assertEqual(gateway.resolve_block('read_file')['source'], 'policy')
        self.assertEqual(gateway.resolve_block('workspace_banner')['reason'], 'deny_prefixes')
        self.assertEqual(gateway.get_before_hooks('list_dir')[0]['content'], 'policy before')
        self.assertEqual(gateway.get_after_hooks('list_dir')[0]['content'], 'policy after')


if __name__ == '__main__':
    unittest.main()

