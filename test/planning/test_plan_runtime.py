"""ISSUE-018 PlanningGateway 计划视图单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_contracts import PlanStep, PlanStepStatus, TaskStatus
from planning import PlanningGateway


class PlanRuntimeTests(unittest.TestCase):
    """验证计划更新、计划到任务同步与清空行为。"""

    def test_update_plan_persists_and_syncs_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))

            gateway.update_plan(
                (
                    PlanStep(step_id='step-001', title='拆分问题'),
                    PlanStep(step_id='step-002', title='实现代码', dependencies=('step-001',)),
                ),
                sync_tasks=True,
            )

            reloaded_gateway = PlanningGateway.from_workspace(str(workspace))
            persisted = json.loads((workspace / '.claw' / 'plan.json').read_text(encoding='utf-8'))

            self.assertEqual([item.step_id for item in reloaded_gateway.list_plan_steps()], ['step-001', 'step-002'])
            self.assertEqual(reloaded_gateway.get_plan_step('step-001').status, PlanStepStatus.PENDING)
            self.assertEqual(reloaded_gateway.get_plan_step('step-002').status, PlanStepStatus.BLOCKED)
            self.assertEqual([item.task_id for item in reloaded_gateway.list_tasks()], ['step-001', 'step-002'])
            self.assertEqual(reloaded_gateway.get_task('step-002').dependencies, ('step-001',))
            self.assertEqual(persisted['schema_version'], 1)
            self.assertEqual(persisted['steps'][0]['step_id'], 'step-001')
            self.assertIn('step-001', reloaded_gateway.render_plan())

    def test_sync_tasks_updates_plan_status_from_task_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))

            gateway.update_plan(
                (
                    PlanStep(step_id='step-001', title='完成前置步骤'),
                    PlanStep(step_id='step-002', title='等待依赖释放', dependencies=('step-001',)),
                ),
                sync_tasks=True,
            )

            gateway.start_task('step-001')
            gateway.complete_task('step-001')

            synced_steps = gateway.sync_tasks_from_plan()

            self.assertEqual([item.step_id for item in synced_steps], ['step-001', 'step-002'])
            self.assertEqual(gateway.get_plan_step('step-001').status, PlanStepStatus.COMPLETED)
            self.assertEqual(gateway.get_plan_step('step-002').status, PlanStepStatus.PENDING)
            self.assertEqual(gateway.get_task('step-002').status, TaskStatus.PENDING)

    def test_clear_plan_sync_clears_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))

            gateway.update_plan((PlanStep(step_id='step-001', title='临时步骤'),), sync_tasks=True)

            gateway.clear_plan(sync_tasks=True)

            reloaded_gateway = PlanningGateway.from_workspace(str(workspace))
            persisted = json.loads((workspace / '.claw' / 'plan.json').read_text(encoding='utf-8'))

            self.assertEqual(reloaded_gateway.list_plan_steps(), ())
            self.assertEqual(reloaded_gateway.list_tasks(), ())
            self.assertEqual(persisted['steps'], [])
            self.assertIn('(none)', reloaded_gateway.render_plan())

    def test_update_plan_replaces_removed_steps_in_task_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))

            gateway.update_plan(
                (
                    PlanStep(step_id='step-001', title='保留步骤'),
                    PlanStep(step_id='step-002', title='待删除步骤'),
                ),
                sync_tasks=True,
            )

            gateway.update_plan((PlanStep(step_id='step-001', title='保留且改名的步骤'),), sync_tasks=True)

            self.assertEqual([item.task_id for item in gateway.list_tasks()], ['step-001'])
            self.assertEqual(gateway.get_task('step-001').title, '保留且改名的步骤')
            with self.assertRaises(ValueError):
                gateway.get_task('step-002')


if __name__ == '__main__':
    unittest.main()
