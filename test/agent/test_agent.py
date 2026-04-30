"""AgentGateway 极简骨架测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from agent import AgentGateway
from core_contracts.config import BudgetConfig
from core_contracts.config import ContextPolicy, ExecutionPolicy, SessionPaths, ToolPermissionPolicy, WorkspaceScope
from core_contracts.model import ModelConfig
from core_contracts.session_contracts import AgentSessionSnapshot


class _DummyClient:
    """满足 ModelClient 协议的最小桩实现。"""

    def complete(self, messages, tools=None, *, output_schema=None):  # pragma: no cover
        raise AssertionError('skeleton gateway should not call model.complete')

    def stream(self, messages, tools=None, *, output_schema=None):  # pragma: no cover
        raise AssertionError('skeleton gateway should not call model.stream')


class AgentGatewaySkeletonTests(unittest.TestCase):
    def _make_gateway(self) -> AgentGateway:
        workspace = Path(__file__).resolve().parent
        return AgentGateway(
            client=_DummyClient(),
            workspace_scope=WorkspaceScope(cwd=workspace),
            execution_policy=ExecutionPolicy(max_turns=8),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            session_paths=SessionPaths(session_directory=workspace / '.tmp-sessions'),
            session_gateway=object(),
            budget_config=BudgetConfig(),
            model_config=ModelConfig(model='dummy-model'),
        )

    def _make_snapshot(self, session_id: str) -> AgentSessionSnapshot:
        workspace = Path(__file__).resolve().parent
        return AgentSessionSnapshot(
            session_id=session_id,
            model_config=ModelConfig(model='dummy-model'),
            workspace_scope=WorkspaceScope(cwd=workspace),
            execution_policy=ExecutionPolicy(max_turns=8),
            context_policy=ContextPolicy(),
            permissions=ToolPermissionPolicy(),
            budget_config=BudgetConfig(),
            session_paths=SessionPaths(session_directory=workspace / '.tmp-sessions'),
            messages=(),
        )

    def test_run_current_stub_raises_type_error_until_loop_is_implemented(self) -> None:
        gateway = self._make_gateway()

        with self.assertRaises(TypeError):
            gateway.run('hello skeleton')

    def test_resume_current_stub_raises_type_error_until_loop_is_implemented(self) -> None:
        gateway = self._make_gateway()
        snapshot = self._make_snapshot('resume-session-001')

        with self.assertRaises(TypeError):
            gateway.resume('resume prompt', snapshot)


if __name__ == '__main__':
    unittest.main()
