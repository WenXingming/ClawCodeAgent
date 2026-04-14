"""ISSUE-005 Shell 安全策略测试。"""

from __future__ import annotations

import unittest

from src.bash_security import (
    SecurityBehavior,
    bash_command_is_safe,
    check_shell_security,
    get_destructive_command_warning,
    is_command_read_only,
    split_command,
)


class BashSecurityTests(unittest.TestCase):
    """验证危险命令识别与权限组合行为。"""

    def test_split_command_supports_chained_segments(self) -> None:
        parts = split_command("echo a && echo b; echo c | findstr c")
        self.assertEqual(parts, ['echo a', 'echo b', 'echo c', 'findstr c'])

    def test_bash_command_is_safe_allows_simple_read_only_command(self) -> None:
        result = bash_command_is_safe('echo hello')
        self.assertEqual(result.behavior, SecurityBehavior.ALLOW)

    def test_bash_command_is_safe_blocks_command_substitution(self) -> None:
        result = bash_command_is_safe('echo $(whoami)')
        self.assertEqual(result.behavior, SecurityBehavior.DENY)
        self.assertIn('substitution', result.message.lower())

    def test_get_destructive_command_warning_detects_rm_rf(self) -> None:
        warning = get_destructive_command_warning('echo ok && rm -rf /tmp/a')
        self.assertIsNotNone(warning)

    def test_is_command_read_only_recognizes_safe_chain(self) -> None:
        self.assertTrue(is_command_read_only('echo hi && dir'))

    def test_check_shell_security_blocks_when_shell_disabled(self) -> None:
        allowed, reason = check_shell_security(
            'echo hi',
            allow_shell=False,
            allow_destructive=False,
        )
        self.assertFalse(allowed)
        self.assertIn('disabled', reason.lower())

    def test_check_shell_security_blocks_destructive_when_unsafe_false(self) -> None:
        allowed, reason = check_shell_security(
            'git reset --hard',
            allow_shell=True,
            allow_destructive=False,
        )
        self.assertFalse(allowed)
        self.assertIn('data loss', reason.lower())

    def test_check_shell_security_allows_destructive_when_unsafe_true(self) -> None:
        allowed, _ = check_shell_security(
            'git reset --hard',
            allow_shell=True,
            allow_destructive=True,
        )
        self.assertTrue(allowed)


if __name__ == '__main__':
    unittest.main()
