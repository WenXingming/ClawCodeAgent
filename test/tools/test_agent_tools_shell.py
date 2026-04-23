"""ISSUE-005 Shell 工具集成测试。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_contracts.config import AgentPermissions, AgentRuntimeConfig
from tools.agent_tools import (
    build_tool_context,
    default_tool_registry,
    execute_tool,
    execute_tool_streaming,
)


class AgentToolsShellTests(unittest.TestCase):
    """验证 bash 工具权限、安全与流式执行行为。"""

    def _build_context(
        self,
        workspace: Path,
        *,
        allow_shell_commands: bool,
        allow_destructive_shell_commands: bool,
        max_output_chars: int = 12000,
        command_timeout_seconds: float = 3.0,
    ):
        config = AgentRuntimeConfig(
            cwd=workspace,
            max_output_chars=max_output_chars,
            command_timeout_seconds=command_timeout_seconds,
            permissions=AgentPermissions(
                allow_shell_commands=allow_shell_commands,
                allow_destructive_shell_commands=allow_destructive_shell_commands,
            ),
        )
        registry = default_tool_registry()
        context = build_tool_context(config, tool_registry=registry)
        return registry, context

    def test_registry_contains_bash_tool(self) -> None:
        registry = default_tool_registry()
        self.assertIn('bash', registry)

    def test_bash_is_blocked_when_shell_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            registry, context = self._build_context(
                workspace,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            )
            result = execute_tool(registry, 'bash', {'command': 'echo hi'}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'permission_denied')

    def test_bash_blocks_destructive_command_when_unsafe_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            registry, context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
            )
            result = execute_tool(registry, 'bash', {'command': 'echo ok && rm -rf /tmp/a'}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'permission_denied')

    def test_bash_executes_safe_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            registry, context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
            )
            result = execute_tool(registry, 'bash', {'command': 'echo hello-shell'}, context)

        self.assertTrue(result.ok)
        self.assertEqual(result.metadata.get('action'), 'bash')
        self.assertIn('exit_code=', result.content)
        self.assertIn('hello-shell', result.content)

    def test_bash_stream_output_can_be_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            registry, context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
            )
            updates = list(
                execute_tool_streaming(
                    registry,
                    'bash',
                    {'command': 'echo alpha && echo beta'},
                    context,
                )
            )

        stdout_text = ''.join(update.chunk for update in updates if update.kind == 'stdout')
        result_updates = [update for update in updates if update.kind == 'result']

        self.assertIn('alpha', stdout_text)
        self.assertIn('beta', stdout_text)
        self.assertEqual(len(result_updates), 1)
        self.assertIsNotNone(result_updates[0].result)
        self.assertTrue(result_updates[0].result.ok)
        self.assertIn('exit_code=', result_updates[0].result.content)

    @patch('tools.agent_tools.subprocess.Popen')
    def test_bash_timeout_returns_structured_error(self, mock_popen: object) -> None:
        process = mock_popen.return_value
        process.communicate.side_effect = subprocess.TimeoutExpired(cmd='sleep', timeout=0.01)

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            registry, context = self._build_context(
                workspace,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
                command_timeout_seconds=0.01,
            )
            result = execute_tool(registry, 'bash', {'command': 'sleep forever'}, context)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata.get('error_kind'), 'tool_execution_error')
        self.assertIn('timed out', result.content)


if __name__ == '__main__':
    unittest.main()
