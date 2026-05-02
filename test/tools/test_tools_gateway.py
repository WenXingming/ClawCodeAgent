"""ToolsGateway 单元测试。

使用 pytest 框架，通过 unittest.mock 对内部组件（ToolExecutor、
DynamicRegistryBuilder、McpOperationsAdapter）进行隔离，验证 Facade 的
委托行为与边界。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from core_contracts.config import ExecutionPolicy, WorkspaceScope
from core_contracts.messaging import ToolExecutionResult
from core_contracts.tools_contracts import (
    McpCapabilityQuery,
    McpResourceQuery,
    ToolPermissionPolicy,
    ToolDescriptor,
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolRegistry,
    ToolStreamUpdate,
)
from tools import ToolsGateway
from tools.executor import ToolExecutor
from tools.mcp_adapter import McpOperationsAdapter
from tools.registry_builder import DynamicRegistryBuilder


# ── Fixtures ────────────────────────────────────────────────────────────────


def _dummy_handler(_args, _ctx):
    return 'ok'


def _dummy_stream_handler(_args, _ctx):
    yield ToolStreamUpdate(kind='stdout', chunk='hello')
    yield ToolStreamUpdate(kind='result', result=ToolExecutionResult(name='t', ok=True, content='done'))


def _make_descriptor(name: str = 'test_tool') -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f'{name} description',
        parameters={'type': 'object', 'properties': {}},
        handler=_dummy_handler,
        stream_handler=_dummy_stream_handler,
    )


def _make_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        root=Path('/tmp'),
        command_timeout_seconds=30.0,
        max_output_chars=12000,
        permissions=ToolPermissionPolicy(),
    )


@pytest.fixture
def mock_local_executor() -> MagicMock:
    return MagicMock(spec=ToolExecutor)


@pytest.fixture
def mock_registry_builder() -> MagicMock:
    return MagicMock(spec=DynamicRegistryBuilder)


@pytest.fixture
def mock_mcp_adapter() -> MagicMock:
    return MagicMock(spec=McpOperationsAdapter)


@pytest.fixture
def gateway(mock_local_executor, mock_registry_builder, mock_mcp_adapter) -> ToolsGateway:
    return ToolsGateway(
        local_executor=mock_local_executor,
        registry_builder=mock_registry_builder,
        mcp_adapter=mock_mcp_adapter,
        tool_registry=ToolRegistry.from_tools(_make_descriptor('seed')),
    )


# ── extend_runtime_registry ─────────────────────────────────────────────────


class TestExtendRuntimeRegistry:
    """验证 extend_runtime_registry 委托到 DynamicRegistryBuilder。"""

    def test_delegates_to_builder(self, gateway: ToolsGateway, mock_registry_builder: MagicMock) -> None:
        base = ToolRegistry.from_tools(_make_descriptor('a'))
        gateway.tool_registry = base
        handlers = {'workspace_search': _dummy_handler}
        mock_registry_builder.build_extended_registry.return_value = ToolRegistry.from_tools(
            _make_descriptor('a'),
            _make_descriptor('ws'),
        )

        result = gateway.extend_runtime_registry(handlers)

        mock_registry_builder.build_extended_registry.assert_called_once_with(base, handlers)
        assert 'a' in result
        assert 'ws' in result


# ── execute_tool ────────────────────────────────────────────────────────────


class TestExecuteTool:
    """验证 execute_tool 委托到 ToolExecutor。"""

    def test_delegates_to_executor(self, gateway: ToolsGateway, mock_local_executor: MagicMock) -> None:
        request = ToolExecutionRequest(
            tool_name='test',
            arguments={'arg': 'val'},
            context=_make_context(),
        )
        registry = ToolRegistry.from_tools(_make_descriptor('test'))
        gateway.tool_registry = registry
        expected = ToolExecutionResult(name='test', ok=True, content='done', metadata={})
        mock_local_executor.execute.return_value = expected

        result = gateway.execute_tool(request)

        mock_local_executor.execute.assert_called_once_with(
            tool_registry=registry,
            name=request.tool_name,
            arguments=request.arguments,
            context=request.context,
        )
        assert result is expected


# ── execute_tool_streaming ──────────────────────────────────────────────────


class TestExecuteToolStreaming:
    """验证 execute_tool_streaming 委托到 ToolExecutor。"""

    def test_delegates_to_executor(self, gateway: ToolsGateway, mock_local_executor: MagicMock) -> None:
        request = ToolExecutionRequest(
            tool_name='test',
            arguments={},
            context=_make_context(),
        )
        registry = ToolRegistry.from_tools(_make_descriptor('test'))
        gateway.tool_registry = registry
        updates = [
            ToolStreamUpdate(kind='stdout', chunk='data'),
            ToolStreamUpdate(kind='result', result=ToolExecutionResult(name='test', ok=True, content='done', metadata={})),
        ]
        mock_local_executor.execute_streaming.return_value = iter(updates)

        result = list(gateway.execute_tool_streaming(request))

        mock_local_executor.execute_streaming.assert_called_once_with(
            tool_registry=registry,
            name=request.tool_name,
            arguments=request.arguments,
            context=request.context,
        )
        assert len(result) == 2


class TestOpenaiToolsProjection:
    """验证 to_openai_tools 通过 ToolRegistry 暴露 schema。"""

    def test_returns_registry_projection(self, gateway: ToolsGateway) -> None:
        gateway.tool_registry = ToolRegistry.from_tools(_make_descriptor('demo'))
        tools = gateway.to_openai_tools()
        assert len(tools) == 1
        assert tools[0]['function']['name'] == 'demo'


# ── list_mcp_resources ──────────────────────────────────────────────────────


class TestListMcpResources:
    """验证 list_mcp_resources 委托到 McpOperationsAdapter。"""

    def test_delegates_to_adapter(self, gateway: ToolsGateway, mock_mcp_adapter: MagicMock) -> None:
        query = McpResourceQuery(query='test', server_name='srv', limit=10)
        expected = ({'uri': 'file:///a'},)
        mock_mcp_adapter.list_resources.return_value = expected

        result = gateway.list_mcp_resources(query)

        mock_mcp_adapter.list_resources.assert_called_once_with(query)
        assert result == expected


# ── search_mcp_capabilities ─────────────────────────────────────────────────


class TestSearchMcpCapabilities:
    """验证 search_mcp_capabilities 委托到 McpOperationsAdapter。"""

    def test_delegates_to_adapter(self, gateway: ToolsGateway, mock_mcp_adapter: MagicMock) -> None:
        query = McpCapabilityQuery(query='deploy', server_name='srv', limit=5)
        expected = ({'handle': 'srv:deploy'},)
        mock_mcp_adapter.search_capabilities.return_value = expected

        result = gateway.search_mcp_capabilities(query)

        mock_mcp_adapter.search_capabilities.assert_called_once_with(query)
        assert result == expected
