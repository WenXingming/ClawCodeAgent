"""Step 8 QueryService 单元测试。"""

from __future__ import annotations

from dataclasses import dataclass
import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from app.app_gateway import AppGateway
from app.query_service import QueryService  # internal import kept for isinstance checks only
from core_contracts.config import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.config import ToolPermissionPolicy
from core_contracts.messaging import OneTurnResponse, ToolCall
from core_contracts.config import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from core_contracts.primitives import TokenUsage
from agent import AgentGateway as Agent
from openai_client.openai_client import OpenAIClient
from session import SessionGateway


@dataclass(frozen=True)
class _RuntimeContracts:
    workspace_scope: WorkspaceScope
    execution_policy: ExecutionPolicy
    context_policy: ContextPolicy
    permissions: ToolPermissionPolicy
    budget_config: BudgetConfig
    session_paths: SessionPaths


class _FakeOpenAIClient(OpenAIClient):
    def __init__(self, responses: list[OneTurnResponse | Exception]) -> None:
        super().__init__(
            ModelConfig(
                model='fake-model',
                base_url='http://127.0.0.1:1/v1',
                api_key='fake-key',
                temperature=0.0,
            )
        )
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools=None, *, output_schema=None):  # type: ignore[override]
        self.calls.append([dict(item) for item in messages])
        if not self._responses:
            raise AssertionError('No prepared response left for test')
        current = self._responses.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class QueryServiceTests(unittest.TestCase):
    def _make_test_dir(self) -> Path:
        workspace = Path(tempfile.mkdtemp(prefix=f'claw-query-service-{uuid4().hex}-'))
        self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
        return workspace

    def _build_runtime_contracts(self, workspace: Path, *, budget: BudgetConfig | None = None) -> _RuntimeContracts:
        return _RuntimeContracts(
            workspace_scope=WorkspaceScope(cwd=workspace),
            execution_policy=ExecutionPolicy(max_turns=6),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(
                allow_file_write=True,
                allow_shell_commands=False,
                allow_destructive_shell_commands=False,
            ),
            budget_config=budget or BudgetConfig(),
            session_paths=SessionPaths(session_directory=workspace / 'sessions'),
        )

    def _build_service(self, workspace: Path, responses: list[OneTurnResponse | Exception], *, budget: BudgetConfig | None = None) -> tuple[QueryService, _FakeOpenAIClient]:
        fake_client = _FakeOpenAIClient(responses)
        contracts = self._build_runtime_contracts(workspace, budget=budget)
        agent = Agent(
            fake_client,
            contracts.workspace_scope,
            contracts.execution_policy,
            contracts.context_policy,
            contracts.permissions,
            contracts.session_paths,
            SessionGateway(contracts.session_paths.session_directory),
            contracts.budget_config,
        )
        return AppGateway.create_query_service(agent), fake_client

    def test_submit_uses_run_then_resume_and_can_return_persisted_session_path(self) -> None:
        workspace = self._make_test_dir()
        service, fake_client = self._build_service(
            workspace,
            [
                OneTurnResponse(
                    content='第一轮回答',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=3, output_tokens=2),
                ),
                OneTurnResponse(
                    content='第二轮回答',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=4, output_tokens=2),
                ),
            ],
        )

        first = service.submit('问题一')
        second = service.submit('问题二')

        self.assertEqual(len(fake_client.calls), 2)
        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(second.output, '第二轮回答')
        self.assertEqual(second.usage.input_tokens, 4)
        self.assertEqual(second.usage.output_tokens, 2)
        self.assertEqual(service.persist_session(), second.session_path)

    def test_stream_submit_emits_runtime_summary_and_message_stop(self) -> None:
        workspace = self._make_test_dir()
        service, _ = self._build_service(
            workspace,
            [
                OneTurnResponse(
                    content='流式回答',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=1),
                ),
            ],
        )

        events = list(service.stream_submit('流式问题'))

        self.assertEqual(events[0]['type'], 'message_start')
        self.assertTrue(any(item.get('type') == 'runtime_summary' for item in events))
        self.assertEqual(events[-1]['type'], 'message_stop')
        self.assertEqual(events[-1]['stop_reason'], 'stop')
        self.assertEqual(events[-1]['usage']['input_tokens'], 2)

    def test_query_service_tracks_delegate_events_and_lineage_stats(self) -> None:
        workspace = self._make_test_dir()
        service, _ = self._build_service(
            workspace,
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(
                            id='delegate_1',
                            name='delegate_agent',
                            arguments={
                                'label': 'demo-group',
                                'tasks': [
                                    {'task_id': 'task-a', 'prompt': '执行子任务 A'},
                                    {
                                        'task_id': 'task-b',
                                        'prompt': '执行子任务 B',
                                        'dependencies': ['task-a'],
                                    },
                                ],
                            },
                        ),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=4, output_tokens=1),
                ),
                OneTurnResponse(
                    content='子任务 A 完成',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=1),
                ),
                OneTurnResponse(
                    content='子任务 B 完成',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=1),
                ),
                OneTurnResponse(
                    content='父任务完成',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ],
        )

        turn = service.submit('执行委托任务')
        summary = service.render_summary()

        self.assertEqual(turn.stop_reason, 'stop')
        self.assertEqual(service.runtime_event_counts.get('delegate_group_start'), 1)
        self.assertEqual(service.runtime_event_counts.get('delegate_child_complete'), 2)
        self.assertEqual(service.runtime_group_status_counts.get('completed'), 1)
        self.assertEqual(service.runtime_child_stop_reason_counts.get('stop'), 2)
        self.assertEqual(service.runtime_lineage_stats.get('unique_groups'), 1)
        self.assertEqual(service.runtime_lineage_stats.get('unique_parent_agents'), 1)
        self.assertEqual(service.runtime_lineage_stats.get('unique_child_agents'), 2)
        self.assertIn('delegate_child_complete=2', summary)
        self.assertIn('unique_child_agents=2', summary)

    def test_query_service_tracks_file_mutation_counts_from_tool_results(self) -> None:
        workspace = self._make_test_dir()
        service, _ = self._build_service(
            workspace,
            [
                OneTurnResponse(
                    content='',
                    tool_calls=(
                        ToolCall(
                            id='write_1',
                            name='write_file',
                            arguments={'path': 'note.txt', 'content': 'hello'},
                        ),
                    ),
                    finish_reason='tool_calls',
                    usage=TokenUsage(input_tokens=3, output_tokens=1),
                ),
                OneTurnResponse(
                    content='写入完成',
                    tool_calls=(),
                    finish_reason='stop',
                    usage=TokenUsage(input_tokens=2, output_tokens=2),
                ),
            ],
        )

        turn = service.submit('写入 note.txt')

        self.assertEqual(turn.stop_reason, 'stop')
        self.assertTrue((workspace / 'note.txt').is_file())
        self.assertEqual(service.runtime_mutation_counts.get('write_file'), 1)


if __name__ == '__main__':
    unittest.main()
