"""ToolExecutor 单元测试。

使用 pytest 框架对 ToolExecutor 进行隔离测试，通过 mock handler 与 stream handler
验证普通执行、流式执行、execute_call 合并及错误包装行为。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core_contracts.config import (
    ExecutionPolicy,
    ToolPermissionPolicy,
    WorkspaceScope,
)
from core_contracts.messaging import ToolExecutionResult
from core_contracts.tools_contracts import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolStreamUpdate,
)
from tools.executor import ToolExecutor, ToolExecutionError, ToolPermissionError


# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_context(**overrides) -> ToolExecutionContext:
    kwargs = dict(
        root=Path('/tmp'),
        command_timeout_seconds=30.0,
        max_output_chars=12000,
        permissions=ToolPermissionPolicy(),
    )
    kwargs.update(overrides)
    return ToolExecutionContext(**kwargs)


@pytest.fixture
def executor() -> ToolExecutor:
    return ToolExecutor()


def _ok_handler(args, ctx) -> str:
    return 'success-output'


def _tuple_handler(args, ctx) -> tuple[str, dict]:
    return 'ok', {'meta': 'data'}


def _error_handler(args, ctx):
    raise ToolExecutionError('execution failure')


def _permission_handler(args, ctx):
    raise ToolPermissionError('not allowed')


def _stream_handler(args, ctx):
    yield ToolStreamUpdate(kind='stdout', chunk='chunk A')
    yield ToolStreamUpdate(kind='stdout', chunk='chunk B')
    yield ToolStreamUpdate(kind='result', result=ToolExecutionResult(name='st', ok=True, content='done'))


def _stream_error_handler(args, ctx):
    yield ToolStreamUpdate(kind='stdout', chunk='start')
    raise ToolExecutionError('mid-stream failure')


def _make_descriptor(name: str, handler, stream_handler=None) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f'{name} desc',
        parameters={'type': 'object', 'properties': {}},
        handler=handler,
        stream_handler=stream_handler,
    )


# ── build_context ───────────────────────────────────────────────────────────


class TestBuildContext:
    """验证 ToolExecutor.build_context 委托到核心工厂函数。"""

    def test_returns_tool_execution_context(self, executor: ToolExecutor) -> None:
        ctx = executor.build_context(
            WorkspaceScope(cwd=Path('/tmp')),
            ExecutionPolicy(),
            ToolPermissionPolicy(),
        )
        assert isinstance(ctx, ToolExecutionContext)
        assert ctx.root == Path('/tmp').resolve()

    def test_forwards_safe_env(self, executor: ToolExecutor) -> None:
        ctx = executor.build_context(
            WorkspaceScope(cwd=Path('/tmp')),
            ExecutionPolicy(),
            ToolPermissionPolicy(),
            safe_env={'KEY': 'VAL'},
        )
        assert ctx.safe_env['KEY'] == 'VAL'


# ── execute ──────────────────────────────────────────────────────────────────


class TestExecute:
    """验证 ToolExecutor.execute 的正常路径与错误包装。"""

    def test_execute_success_string_handler(self, executor: ToolExecutor) -> None:
        registry = {'t': _make_descriptor('t', _ok_handler)}
        result = executor.execute(registry, 't', {}, _make_context())
        assert result.ok
        assert result.name == 't'
        assert result.content == 'success-output'

    def test_execute_success_tuple_handler(self, executor: ToolExecutor) -> None:
        registry = {'t': _make_descriptor('t', _tuple_handler)}
        result = executor.execute(registry, 't', {}, _make_context())
        assert result.ok
        assert result.content == 'ok'
        assert result.metadata == {'meta': 'data'}

    def test_execute_unknown_tool(self, executor: ToolExecutor) -> None:
        registry = {}
        result = executor.execute(registry, 'unknown', {}, _make_context())
        assert not result.ok
        assert result.metadata['error_kind'] == 'unknown_tool'
        assert 'unknown' in result.content

    def test_execute_tool_execution_error(self, executor: ToolExecutor) -> None:
        registry = {'t': _make_descriptor('t', _error_handler)}
        result = executor.execute(registry, 't', {}, _make_context())
        assert not result.ok
        assert result.metadata['error_kind'] == 'tool_execution_error'
        assert 'execution failure' in result.content

    def test_execute_permission_error(self, executor: ToolExecutor) -> None:
        registry = {'t': _make_descriptor('t', _permission_handler)}
        result = executor.execute(registry, 't', {}, _make_context())
        assert not result.ok
        assert result.metadata['error_kind'] == 'permission_denied'
        assert 'not allowed' in result.content


# ── execute_streaming ────────────────────────────────────────────────────────


class TestExecuteStreaming:
    """验证 ToolExecutor.execute_streaming 的各种路径。"""

    def test_streaming_with_handler(self, executor: ToolExecutor) -> None:
        registry = {'st': _make_descriptor('st', _ok_handler, _stream_handler)}
        updates = list(executor.execute_streaming(registry, 'st', {}, _make_context()))
        assert len(updates) == 3
        assert updates[0].kind == 'stdout'
        assert updates[0].chunk == 'chunk A'
        assert updates[1].chunk == 'chunk B'
        assert updates[2].kind == 'result'
        assert updates[2].result.ok

    def test_streaming_without_handler_falls_back_to_execute(self, executor: ToolExecutor) -> None:
        registry = {'st': _make_descriptor('st', _ok_handler)}
        updates = list(executor.execute_streaming(registry, 'st', {}, _make_context()))
        assert len(updates) == 1
        assert updates[0].kind == 'result'
        assert updates[0].result.ok
        assert updates[0].result.content == 'success-output'

    def test_streaming_unknown_tool(self, executor: ToolExecutor) -> None:
        registry = {}
        updates = list(executor.execute_streaming(registry, 'unknown', {}, _make_context()))
        assert len(updates) == 1
        assert updates[0].kind == 'result'
        assert not updates[0].result.ok
        assert 'unknown' in updates[0].result.content

    def test_streaming_error_mid_stream(self, executor: ToolExecutor) -> None:
        registry = {'st': _make_descriptor('st', _ok_handler, _stream_error_handler)}
        updates = list(executor.execute_streaming(registry, 'st', {}, _make_context()))
        result_updates = [u for u in updates if u.kind == 'result']
        assert len(result_updates) == 1
        assert not result_updates[0].result.ok
        assert 'mid-stream' in result_updates[0].result.content


# ── execute_call ─────────────────────────────────────────────────────────────


class TestExecuteCall:
    """验证 ToolExecutor.execute_call 的流式合并与回调行为。"""

    def test_execute_call_returns_final_result(self, executor: ToolExecutor) -> None:
        registry = {'st': _make_descriptor('st', _ok_handler, _stream_handler)}
        result = executor.execute_call(registry, 'st', {}, _make_context())
        assert result.ok
        assert result.content == 'done'

    def test_execute_call_invokes_on_stream_update(self, executor: ToolExecutor) -> None:
        registry = {'st': _make_descriptor('st', _ok_handler, _stream_handler)}
        callbacks: list[ToolStreamUpdate] = []

        result = executor.execute_call(
            registry, 'st', {}, _make_context(), on_stream_update=callbacks.append
        )
        assert result.ok
        assert len(callbacks) == 2
        assert all(u.kind in ('stdout',) for u in callbacks)

    def test_execute_call_no_on_stream_update_does_not_crash(self, executor: ToolExecutor) -> None:
        registry = {'st': _make_descriptor('st', _ok_handler, _stream_handler)}
        result = executor.execute_call(registry, 'st', {}, _make_context())
        assert result.ok

    def test_execute_call_no_final_result_returns_fallback(self, executor: ToolExecutor) -> None:
        def _stream_no_result(args, ctx):
            yield ToolStreamUpdate(kind='stdout', chunk='data')
            # no result update

        registry = {'st': _make_descriptor('st', _ok_handler, _stream_no_result)}
        result = executor.execute_call(registry, 'st', {}, _make_context())
        assert not result.ok
        assert 'no final result' in result.content
        assert result.metadata['error_kind'] == 'tool_execution_error'
