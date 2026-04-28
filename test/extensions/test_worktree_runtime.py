"""ISSUE-023 Worktree Runtime 单元测试。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from workspace import WorktreeHistoryAction, WorktreeService, WorktreeStatus


class WorktreeServiceTests(unittest.TestCase):
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

            runtime = WorktreeService.from_workspace(workspace)
            record = runtime.enter_worktree('feature/worktree-enter')
            persisted = json.loads((workspace / '.claw' / 'worktree_state.json').read_text(encoding='utf-8'))
            branch_name = self._run_git(record.path, 'branch', '--show-current').stdout.strip()

            self.assertEqual(record.status, WorktreeStatus.ACTIVE)
            self.assertEqual(branch_name, 'feature/worktree-enter')
            self.assertEqual(runtime.current_cwd, record.path)
            self.assertTrue(record.path.is_dir())
            self.assertEqual(persisted['current_cwd'], str(record.path))
            self.assertEqual(persisted['managed_worktrees'][0]['branch'], 'feature/worktree-enter')

    def test_exit_worktree_keep_restores_workspace_cwd_and_preserves_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._init_repo(workspace)

            runtime = WorktreeService.from_workspace(workspace)
            entered = runtime.enter_worktree('feature/worktree-keep')
            exited = runtime.exit_worktree(remove=False)
            reloaded = WorktreeService.from_workspace(workspace)

        self.assertEqual(exited.status, WorktreeStatus.EXITED)
        self.assertEqual(runtime.current_cwd, workspace.resolve())
        self.assertEqual(reloaded.current_cwd, workspace.resolve())
        self.assertTrue(entered.path.is_dir())
        self.assertIsNone(reloaded.active_worktree())
        self.assertEqual(reloaded.list_worktrees()[0].status, WorktreeStatus.EXITED)

    def test_exit_worktree_remove_deletes_directory_and_records_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._init_repo(workspace)

            runtime = WorktreeService.from_workspace(workspace)
            entered = runtime.enter_worktree('feature/worktree-remove')
            removed = runtime.exit_worktree(remove=True)
            persisted_history = json.loads(
                (workspace / '.claw' / 'worktree_history.json').read_text(encoding='utf-8')
            )
            listed_worktrees = self._run_git(workspace, 'worktree', 'list', '--porcelain').stdout

            self.assertEqual(removed.status, WorktreeStatus.REMOVED)
            self.assertFalse(entered.path.exists())
            self.assertNotIn(str(entered.path), listed_worktrees)
            self.assertEqual([event['action'] for event in persisted_history['events']], ['enter', 'exit_remove'])

    def test_exit_worktree_remove_blocks_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._init_repo(workspace)

            runtime = WorktreeService.from_workspace(workspace)
            entered = runtime.enter_worktree('feature/worktree-dirty')
            (entered.path / 'README.md').write_text('dirty change\n', encoding='utf-8')

            with self.assertRaisesRegex(ValueError, 'dirty worktree'):
                runtime.exit_worktree(remove=True)

            reloaded = WorktreeService.from_workspace(workspace)

        self.assertTrue(entered.path.exists())
        self.assertIsNotNone(reloaded.active_worktree())
        self.assertEqual(reloaded.active_worktree().status, WorktreeStatus.ACTIVE)
        self.assertEqual(reloaded.history_records[0].action, WorktreeHistoryAction.ENTER)


if __name__ == '__main__':
    unittest.main()