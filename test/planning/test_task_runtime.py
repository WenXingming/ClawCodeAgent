"""ISSUE-017 PlanningGateway 任务视图单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_contracts import TaskStatus
from backups.planning import PlanningGateway


class TaskRuntimeTests(unittest.TestCase):
    """验证任务状态流转、依赖阻塞与持久化。"""

    def test_task_status_transitions_validate_legal_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))
            task = gateway.create_task('task-001', '实现 Task Runtime')

            self.assertEqual(task.status, TaskStatus.PENDING)

            with self.assertRaises(ValueError):
                gateway.complete_task('task-001')

            started = gateway.start_task('task-001')
            self.assertEqual(started.status, TaskStatus.IN_PROGRESS)

            with self.assertRaises(ValueError):
                gateway.start_task('task-001')

            completed = gateway.complete_task('task-001')
            self.assertEqual(completed.status, TaskStatus.COMPLETED)

            with self.assertRaises(ValueError):
                gateway.cancel_task('task-001')

    def test_dependencies_block_and_release_on_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))
            gateway.create_task('task-001', '先完成前置任务')
            dependent = gateway.create_task('task-002', '等待前置任务完成', dependencies=('task-001',))

            self.assertEqual(dependent.status, TaskStatus.BLOCKED)
            self.assertEqual(dependent.blocked_by, ('task-001',))
            self.assertEqual([item.task_id for item in gateway.next_tasks()], ['task-001'])

            gateway.start_task('task-001')
            gateway.complete_task('task-001')

            released = gateway.get_task('task-002')
            self.assertEqual(released.status, TaskStatus.PENDING)
            self.assertEqual(released.blocked_by, ())

    def test_next_tasks_skip_manual_blocks_and_cancelled_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))
            gateway.create_task('task-001', '当前可执行任务')
            gateway.create_task('task-002', '人工阻塞任务')
            gateway.create_task('task-003', '依赖 task-001 的任务', dependencies=('task-001',))

            gateway.block_task('task-002', reason='等待外部输入')

            self.assertEqual([item.task_id for item in gateway.next_tasks()], ['task-001'])

            gateway.start_task('task-001')
            gateway.complete_task('task-001')

            self.assertEqual([item.task_id for item in gateway.next_tasks()], ['task-003'])

            gateway.cancel_task('task-003')
            self.assertEqual(gateway.next_tasks(), ())

    def test_save_and_reload_round_trip_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))
            gateway.create_task('task-001', '持久化任务', description='写入并恢复任务状态')
            gateway.start_task('task-001')

            reloaded_gateway = PlanningGateway.from_workspace(str(workspace))
            restored = reloaded_gateway.get_task('task-001')
            persisted_path = workspace / '.claw' / 'tasks.json'
            payload = json.loads(persisted_path.read_text(encoding='utf-8'))

            self.assertEqual(restored.status, TaskStatus.IN_PROGRESS)
            self.assertEqual(restored.description, '写入并恢复任务状态')
            self.assertEqual(payload['schema_version'], 1)
            self.assertEqual(payload['tasks'][0]['task_id'], 'task-001')

    def test_update_task_changes_fields_and_list_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            gateway = PlanningGateway.from_workspace(str(workspace))
            gateway.create_task('task-001', '首个任务')
            gateway.create_task('task-002', '第二个任务')

            updated = gateway.update_task(
                'task-002',
                title='更新后的第二个任务',
                description='补充描述',
                dependencies=('task-001',),
            )

            self.assertEqual(updated.title, '更新后的第二个任务')
            self.assertEqual(updated.description, '补充描述')
            self.assertEqual(updated.status, TaskStatus.BLOCKED)
            self.assertEqual(updated.blocked_by, ('task-001',))
            self.assertEqual([item.task_id for item in gateway.list_tasks()], ['task-001', 'task-002'])


if __name__ == '__main__':
    unittest.main()
