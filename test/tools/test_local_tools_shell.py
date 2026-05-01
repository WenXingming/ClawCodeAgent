"""ISSUE-005 Shell 工具集成测试。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core_contracts.config import ExecutionPolicy, ToolPermissionPolicy, WorkspaceScope
from core_contracts.tools_contracts import ToolExecutionRequest, build_execution_context
from tools import ToolsGatewayFactory
from tools.local.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutor
from tools.mcp_adapter import McpOperationsAdapter
from tools.registry_builder import DynamicRegistryBuilder
from tools.tools_gateway import ToolsGateway


class LocalToolsShellTests(unittest.TestCase):
    """验证 bash 工具权限、安全与流式执行行为。"""

    def setUp(self) -> None:
        self.registry = ToolsGatewayFactory.create_default_registry(ShellSecurityPolicy())
        self.gateway = ToolsGateway(
            local_executor=ToolExecutor(),
            registry_builder=MagicMock(spec=DynamicRegistryBuilder),
            mcp_adapter=MagicMock(spec=McpOperationsAdapter),
        )

    def _build_context(
        self,
        workspace: Path,
        *,
        allow_shell_commands: bool,
        allow_destructive_shell_commands: bool,
        max_output_chars: int = 12000,
        command_timeout_seconds: float = 3.0,
        safe_env: dict[str, str] | None = None,
    ):
        return build_execution_context(
            WorkspaceScope(cwd=workspace),
            ExecutionPolicy(max_output_chars=max_output_chars, command_timeout_seconds=command_timeout_seconds),
            ToolPermissionPolicy(
                allow_shell_commands=allow_shell_commands,
                allow_destructive_shell_commands=allow_destructive_shell_commands,
            ),
            tool_registry=self.registry,
            safe_env=safe_env,
        )

    def _execute(self, tool_name: str, arguments: dict, context) -> object:
        request = ToolExecutionRequest(tool_name=tool_name, arguments=arguments, context=context)
        return self.gateway.execute_tool(request, self.registry)

    def _execute_streaming(self, tool_name: str, arguments: dict, context) -> list:
        request = ToolExecutionRequest(tool_name=tool_name, arguments=arguments, context=context)
        return list(self.gateway.execute_tool_streaming(request, self.registry))

    def test_registry_contains_bash_tool(self) -> None:
        self.assertIn('bash', self.registry)

    def test_bash_is_blocked_when_shell_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(
                workspace,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            )
            result = self._execute('bash', {'command': 'echo hi'}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'permission_denied')

    def test_bash_blocks_destructive_command_when_unsafe_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
            )
            result = self._execute('bash', {'command': 'echo ok && rm -rf /tmp/a'}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'permission_denied')

    def test_bash_executes_safe_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
            )
            result = self._execute('bash', {'command': 'echo hello-shell'}, context)

        self.assertTrue(result.ok)
        self.assertEqual(result.metadata.get('action'), 'bash')
        self.assertIn('exit_code=', result.content)
        self.assertIn('hello-shell', result.content)

    def test_bash_stream_output_can_be_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
            )
            updates = self._execute_streaming('bash', {'command': 'echo alpha && echo beta'}, context)

        stdout_text = ''.join(update.chunk for update in updates if update.kind == 'stdout')
        result_updates = [update for update in updates if update.kind == 'result']
        stdout_positions = [index for index, update in enumerate(updates) if update.kind == 'stdout']
        result_position = next(index for index, update in enumerate(updates) if update.kind == 'result')

        self.assertIn('alpha', stdout_text)
        self.assertIn('beta', stdout_text)
        self.assertEqual(len(result_updates), 1)
        self.assertIsNotNone(result_updates[0].result)
        self.assertTrue(result_updates[0].result.ok)
        self.assertIn('exit_code=', result_updates[0].result.content)
        self.assertLess(max(stdout_positions), result_position)

    @patch('tools.local.shell_tools.subprocess.Popen')
    def test_bash_timeout_returns_structured_error(self, mock_popen: object) -> None:
        process = mock_popen.return_value
        process.communicate.side_effect = subprocess.TimeoutExpired(cmd='sleep', timeout=0.01)

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
                command_timeout_seconds=0.01,
            )
            result = self._execute('bash', {'command': 'sleep forever'}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'tool_execution_error')
        self.assertIn('timed out', result.content)

    @patch('tools.local.shell_tools.subprocess.Popen')
    def test_bash_passes_safe_env_to_subprocess(self, mock_popen: object) -> None:
        process = mock_popen.return_value
        process.communicate.return_value = ('hello', '')
        process.returncode = 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
                safe_env={'POLICY_FLAG': 'enabled'},
            )
            result = self._execute('bash', {'command': 'echo hello'}, context)

        self.assertTrue(result.ok)
        self.assertEqual(mock_popen.call_args.kwargs['env']['POLICY_FLAG'], 'enabled')


if __name__ == '__main__':
    unittest.main()
