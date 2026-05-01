"""ISSUE-023 WorkspaceGateway worktree 能力单元测试。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from backups.workspace import WorkspaceGateway


class WorkspaceWorktreeGatewayTests(unittest.TestCase):
    def _run_git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ['git', *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            check=True,
        )

    def _init_repo(self, workspace: Path) -> None:
        self._run_git(workspace, 'init')
        self._run_git(workspace, 'config', 'user.name', 'ClawCodeAgent Test')
        self._run_git(workspace, 'config', 'user.email', 'tests@example.com')
        (workspace / 'README.md').write_text('seed\n', encoding='utf-8')
        self._run_git(workspace, 'add', 'README.md')
        self._run_git(workspace, 'commit', '-m', 'init')

    def test_enter_worktree_creates_branch_and_switches_current_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._init_repo(workspace)

            gateway = WorkspaceGateway.from_workspace(workspace)
            record = gateway.enter_worktree('feature/worktree-enter')
            persisted = json.loads((workspace / '.claw' / 'worktree_state.json').read_text(encoding='utf-8'))
            branch_name = self._run_git(Path(record['path']), 'branch', '--show-current').stdout.strip()

            self.assertEqual(record['status'], 'active')
            self.assertEqual(branch_name, 'feature/worktree-enter')
            self.assertEqual(gateway.current_worktree_cwd(), record['path'])
            self.assertTrue(Path(record['path']).is_dir())
            self.assertEqual(persisted['current_cwd'], record['path'])
            self.assertEqual(persisted['managed_worktrees'][0]['branch'], 'feature/worktree-enter')

    def test_exit_worktree_keep_restores_workspace_cwd_and_preserves_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._init_repo(workspace)

            gateway = WorkspaceGateway.from_workspace(workspace)
            entered = gateway.enter_worktree('feature/worktree-keep')
            exited = gateway.exit_worktree(remove=False)
            reloaded = WorkspaceGateway.from_workspace(workspace)

        self.assertEqual(exited['status'], 'exited')
        self.assertEqual(gateway.current_worktree_cwd(), str(workspace.resolve()))
        self.assertEqual(reloaded.current_worktree_cwd(), str(workspace.resolve()))
        self.assertTrue(Path(entered['path']).is_dir())
        self.assertIsNone(reloaded.active_worktree())
        self.assertEqual(reloaded.list_worktrees()[0]['status'], 'exited')

    def test_exit_worktree_remove_deletes_directory_and_records_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._init_repo(workspace)

            gateway = WorkspaceGateway.from_workspace(workspace)
            entered = gateway.enter_worktree('feature/worktree-remove')
            removed = gateway.exit_worktree(remove=True)
            persisted_history = json.loads(
                (workspace / '.claw' / 'worktree_history.json').read_text(encoding='utf-8')
            )
            listed_worktrees = self._run_git(workspace, 'worktree', 'list', '--porcelain').stdout

            self.assertEqual(removed['status'], 'removed')
            self.assertFalse(Path(entered['path']).exists())
            self.assertNotIn(entered['path'], listed_worktrees)
            self.assertEqual([event['action'] for event in persisted_history['events']], ['enter', 'exit_remove'])

    def test_exit_worktree_remove_blocks_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._init_repo(workspace)

            gateway = WorkspaceGateway.from_workspace(workspace)
            entered = gateway.enter_worktree('feature/worktree-dirty')
            (Path(entered['path']) / 'README.md').write_text('dirty change\n', encoding='utf-8')

            with self.assertRaisesRegex(ValueError, 'dirty worktree'):
                gateway.exit_worktree(remove=True)

            reloaded = WorkspaceGateway.from_workspace(workspace)

        self.assertTrue(Path(entered['path']).exists())
        self.assertIsNotNone(reloaded.active_worktree())
        self.assertEqual(reloaded.active_worktree()['status'], 'active')


if __name__ == '__main__':
    unittest.main()
