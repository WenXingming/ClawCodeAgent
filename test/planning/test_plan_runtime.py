"""ISSUE-018 Plan Runtime 单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from planning.plan_runtime import PlanRuntime, PlanStep, PlanStepStatus
from planning.task_runtime import TaskRuntime, TaskStatus


class PlanRuntimeTests(unittest.TestCase):
    """验证计划更新、计划-任务同步与清空行为。"""

    def test_update_plan_persists_and_syncs_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = PlanRuntime.from_workspace(workspace)

            runtime.update_plan(
                (
                    PlanStep(step_id='step-001', title='拆分问题'),
                    PlanStep(step_id='step-002', title='实现代码', dependencies=('step-001',)),
                ),
                sync_tasks=True,
            )

            reloaded = PlanRuntime.from_workspace(workspace)
            task_runtime = TaskRuntime.from_workspace(workspace)
            persisted = json.loads((workspace / '.claw' / 'plan.json').read_text(encoding='utf-8'))

            self.assertEqual([item.step_id for item in reloaded.list_steps()], ['step-001', 'step-002'])
            self.assertEqual(reloaded.get_step('step-001').status, PlanStepStatus.PENDING)
            self.assertEqual(reloaded.get_step('step-002').status, PlanStepStatus.BLOCKED)
            self.assertEqual([item.task_id for item in task_runtime.list_tasks()], ['step-001', 'step-002'])
            self.assertEqual(task_runtime.get_task('step-002').dependencies, ('step-001',))
            self.assertEqual(persisted['schema_version'], 1)
            self.assertEqual(persisted['steps'][0]['step_id'], 'step-001')
            self.assertIn('step-001', reloaded.render_plan())

    def test_sync_tasks_updates_plan_status_from_task_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = PlanRuntime.from_workspace(workspace)

            runtime.update_plan(
                (
                    PlanStep(step_id='step-001', title='完成前置步骤'),
                    PlanStep(step_id='step-002', title='等待依赖释放', dependencies=('step-001',)),
                ),
                sync_tasks=True,
            )

            task_runtime = TaskRuntime.from_workspace(workspace)
            task_runtime.start_task('step-001')
            task_runtime.complete_task('step-001')

            synced_steps = runtime.sync_tasks()

            self.assertEqual([item.step_id for item in synced_steps], ['step-001', 'step-002'])
            self.assertEqual(runtime.get_step('step-001').status, PlanStepStatus.COMPLETED)
            self.assertEqual(runtime.get_step('step-002').status, PlanStepStatus.PENDING)
            self.assertEqual(TaskRuntime.from_workspace(workspace).get_task('step-002').status, TaskStatus.PENDING)

    def test_clear_plan_sync_clears_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = PlanRuntime.from_workspace(workspace)

            runtime.update_plan(
                (
                    PlanStep(step_id='step-001', title='临时步骤'),
                ),
                sync_tasks=True,
            )

            runtime.clear_plan(sync_tasks=True)

            reloaded = PlanRuntime.from_workspace(workspace)
            task_runtime = TaskRuntime.from_workspace(workspace)
            persisted = json.loads((workspace / '.claw' / 'plan.json').read_text(encoding='utf-8'))

            self.assertEqual(reloaded.list_steps(), ())
            self.assertEqual(task_runtime.list_tasks(), ())
            self.assertEqual(persisted['steps'], [])
            self.assertIn('(none)', reloaded.render_plan())

    def test_update_plan_replaces_removed_steps_in_task_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = PlanRuntime.from_workspace(workspace)

            runtime.update_plan(
                (
                    PlanStep(step_id='step-001', title='保留步骤'),
                    PlanStep(step_id='step-002', title='待删除步骤'),
                ),
                sync_tasks=True,
            )

            runtime.update_plan(
                (
                    PlanStep(step_id='step-001', title='保留且改名的步骤'),
                ),
                sync_tasks=True,
            )

            task_runtime = TaskRuntime.from_workspace(workspace)

            self.assertEqual([item.task_id for item in task_runtime.list_tasks()], ['step-001'])
            self.assertEqual(task_runtime.get_task('step-001').title, '保留且改名的步骤')
            with self.assertRaises(ValueError):
                task_runtime.get_task('step-002')


if __name__ == '__main__':
    unittest.main()