"""ISSUE-024 AgentManager 单元测试。"""

from __future__ import annotations

import unittest

from orchestration.agent_manager import AgentManager, DelegatedTaskSpec, ManagedAgentStatus


class AgentManagerTests(unittest.TestCase):
    def test_plan_batches_respects_dependencies_and_input_order(self) -> None:
        manager = AgentManager()
        tasks = (
            DelegatedTaskSpec(task_id='task-a', prompt='执行 A'),
            DelegatedTaskSpec(task_id='task-b', prompt='执行 B'),
            DelegatedTaskSpec(task_id='task-c', prompt='执行 C', dependencies=('task-a',)),
            DelegatedTaskSpec(task_id='task-d', prompt='执行 D', dependencies=('task-a', 'task-b')),
            DelegatedTaskSpec(task_id='task-e', prompt='执行 E', dependencies=('task-c',)),
        )

        batches = manager.plan_batches(tasks)

        self.assertEqual([[item.task_id for item in batch] for batch in batches], [
            ['task-a', 'task-b'],
            ['task-c', 'task-d'],
            ['task-e'],
        ])

    def test_plan_batches_rejects_unknown_and_circular_dependencies(self) -> None:
        manager = AgentManager()

        with self.assertRaisesRegex(ValueError, 'unknown tasks'):
            manager.plan_batches((
                DelegatedTaskSpec(task_id='task-a', prompt='执行 A', dependencies=('missing',)),
            ))

        with self.assertRaisesRegex(ValueError, 'Circular delegated task dependencies'):
            manager.plan_batches((
                DelegatedTaskSpec(task_id='task-a', prompt='执行 A', dependencies=('task-b',)),
                DelegatedTaskSpec(task_id='task-b', prompt='执行 B', dependencies=('task-a',)),
            ))

    def test_group_summary_counts_stop_reasons_resume_and_skips(self) -> None:
        manager = AgentManager()
        parent_id = manager.start_agent(prompt='父任务')
        group_id = manager.start_group(label='delegation', parent_agent_id=parent_id)

        child_a = manager.start_agent(
            prompt='子任务 A',
            parent_agent_id=parent_id,
            group_id=group_id,
            child_index=0,
            task_id='task-a',
        )
        child_b = manager.start_agent(
            prompt='子任务 B',
            parent_agent_id=parent_id,
            group_id=group_id,
            child_index=1,
            task_id='task-b',
            resumed_from_session_id='session-b',
        )
        child_c = manager.start_agent(
            prompt='子任务 C',
            parent_agent_id=parent_id,
            group_id=group_id,
            child_index=2,
            task_id='task-c',
        )

        manager.finish_agent(
            child_a,
            session_id='session-a',
            session_path='sessions/a.json',
            turns=2,
            tool_calls=1,
            stop_reason='completed',
        )
        manager.finish_agent(
            child_b,
            session_id='session-b',
            session_path='sessions/b.json',
            turns=1,
            tool_calls=0,
            stop_reason='backend_error',
        )
        manager.skip_agent(child_c, reason='dependency_skipped')
        manager.finish_group(
            group_id,
            status='completed_with_failures',
            completed_children=1,
            failed_children=1,
            batch_count=2,
            max_batch_size=2,
            dependency_skips=1,
        )

        summary = manager.group_summary(group_id)

        self.assertEqual(summary['child_count'], 3)
        self.assertEqual(summary['resumed_children'], 1)
        self.assertEqual(summary['dependency_skips'], 1)
        self.assertEqual(summary['failed_children'], 1)
        self.assertEqual(summary['stop_reason_counts']['completed'], 1)
        self.assertEqual(summary['stop_reason_counts']['backend_error'], 1)
        self.assertEqual(summary['stop_reason_counts']['dependency_skipped'], 1)
        self.assertEqual(manager.records[child_c].status, ManagedAgentStatus.SKIPPED)


if __name__ == '__main__':
    unittest.main()