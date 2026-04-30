"""QueryService 与当前骨架 Agent 的兼容性测试。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from agent import AgentGateway as Agent
from app.app_gateway import AppGateway
from core_contracts.config import BudgetConfig
from core_contracts.config import ContextPolicy, ExecutionPolicy, SessionPaths, ToolPermissionPolicy, WorkspaceScope
from core_contracts.model import ModelConfig
from session import create_session_gateway


class _DummyClient:
    def complete(self, messages, tools=None, *, output_schema=None):  # pragma: no cover
        raise AssertionError('skeleton gateway should not call model.complete')

    def stream(self, messages, tools=None, *, output_schema=None):  # pragma: no cover
        raise AssertionError('skeleton gateway should not call model.stream')


class QueryServiceTests(unittest.TestCase):
    def _make_service(self):
        workspace = Path(tempfile.mkdtemp(prefix=f'claw-query-service-{uuid4().hex}-'))
        self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)

        agent = Agent(
            _DummyClient(),
            WorkspaceScope(cwd=workspace),
            ExecutionPolicy(max_turns=4),
            ContextPolicy(),
            ToolPermissionPolicy(),
            SessionPaths(session_directory=workspace / 'sessions'),
            create_session_gateway(workspace / 'sessions'),
            BudgetConfig(),
            ModelConfig(model='dummy-model'),
        )
        return AppGateway.create_query_service(agent)

    def test_submit_raises_type_error_when_agent_stub_returns_invalid_result(self) -> None:
        service = self._make_service()

        with self.assertRaises(TypeError):
            service.submit('hello')

    def test_stream_submit_raises_type_error_when_agent_stub_returns_invalid_result(self) -> None:
        service = self._make_service()

        with self.assertRaises(TypeError):
            list(service.stream_submit('hello'))


if __name__ == '__main__':
    unittest.main()
