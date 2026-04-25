"""ISSUE-019 Workflow Runtime 单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.task_runtime import TaskRuntime, TaskStatus
from runtime.workflow_runtime import WorkflowRuntime, WorkflowRunStatus


class WorkflowRuntimeTests(unittest.TestCase):
    """验证 workflow manifest 发现、运行与历史记录。"""

    def _write_manifest(self, workspace: Path, filename: str, payload: dict[str, object]) -> None:
        manifest_dir = workspace / '.claw' / 'workflows'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def test_from_workspace_discovers_workflow_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'demo.json',
                {
                    'workflow_id': 'demo-workflow',
                    'title': 'Demo Workflow',
                    'steps': [
                        {'action': 'create', 'task_id': 'task-001', 'title': '准备任务'},
                    ],
                },
            )

            runtime = WorkflowRuntime.from_workspace(workspace)

        self.assertEqual([item.workflow_id for item in runtime.list_workflows()], ['demo-workflow'])
        self.assertEqual(runtime.get_workflow('demo-workflow').title, 'Demo Workflow')
        self.assertEqual(runtime.get_workflow('demo-workflow').steps[0].action.value, 'create')

    def test_run_workflow_records_success_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'success.json',
                {
                    'workflow_id': 'success-workflow',
                    'title': 'Success Workflow',
                    'steps': [
                        {'action': 'create', 'task_id': 'task-001', 'title': '前置任务'},
                        {
                            'action': 'create',
                            'task_id': 'task-002',
                            'title': '后续任务',
                            'dependencies': ['task-001'],
                        },
                        {'action': 'start', 'task_id': 'task-001'},
                        {'action': 'complete', 'task_id': 'task-001'},
                        {'action': 'start', 'task_id': 'task-002'},
                        {'action': 'complete', 'task_id': 'task-002'},
                    ],
                },
            )

            runtime = WorkflowRuntime.from_workspace(workspace)
            run_record = runtime.run_workflow('success-workflow')
            task_runtime = TaskRuntime.from_workspace(workspace)
            reloaded = WorkflowRuntime.from_workspace(workspace)
            persisted = json.loads((workspace / '.claw' / 'workflow_runs.json').read_text(encoding='utf-8'))

        self.assertEqual(run_record.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(len(run_record.step_results), 6)
        self.assertEqual(task_runtime.get_task('task-001').status, TaskStatus.COMPLETED)
        self.assertEqual(task_runtime.get_task('task-002').status, TaskStatus.COMPLETED)
        self.assertEqual(len(reloaded.history('success-workflow')), 1)
        self.assertEqual(reloaded.history('success-workflow')[0].run_id, run_record.run_id)
        self.assertEqual(persisted['runs'][0]['workflow_id'], 'success-workflow')
        self.assertEqual(persisted['runs'][0]['status'], 'succeeded')

    def test_run_workflow_records_failure_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'failure.json',
                {
                    'workflow_id': 'failure-workflow',
                    'title': 'Failure Workflow',
                    'steps': [
                        {'action': 'create', 'task_id': 'task-001', 'title': '未开始任务'},
                        {'action': 'complete', 'task_id': 'task-001'},
                    ],
                },
            )

            runtime = WorkflowRuntime.from_workspace(workspace)
            run_record = runtime.run_workflow('failure-workflow')
            task_runtime = TaskRuntime.from_workspace(workspace)
            reloaded = WorkflowRuntime.from_workspace(workspace)

        self.assertEqual(run_record.status, WorkflowRunStatus.FAILED)
        self.assertIn('cannot complete from status', run_record.error_message or '')
        self.assertEqual(len(run_record.step_results), 2)
        self.assertTrue(run_record.step_results[0].ok)
        self.assertFalse(run_record.step_results[1].ok)
        self.assertIn('cannot complete from status', run_record.step_results[1].error or '')
        self.assertEqual(task_runtime.get_task('task-001').status, TaskStatus.PENDING)
        self.assertEqual(reloaded.history('failure-workflow')[0].status, WorkflowRunStatus.FAILED)


if __name__ == '__main__':
    unittest.main()