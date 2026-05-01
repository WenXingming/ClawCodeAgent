"""tools 领域统一网关。

该文件是 tools 子系统对外唯一边界，提供极致清爽的 Facade 模式。
所有复杂业务均委托给注入的内部组件（Adapter, Builder, Executor）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator, Mapping

from core_contracts.messaging import ToolExecutionResult
from core_contracts.primitives import JSONDict
from core_contracts.tools_contracts import (
    McpCapabilityQuery,
    McpResourceQuery,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolStreamUpdate,
)
from tools.executor import ToolExecutor
from tools.mcp_adapter import McpOperationsAdapter
from tools.registry_builder import DynamicRegistryBuilder


@dataclass(frozen=True)
class ToolsGateway:
    """tools 领域唯一对外入口。"""

    local_executor: ToolExecutor
    registry_builder: DynamicRegistryBuilder
    mcp_adapter: McpOperationsAdapter

    def extend_runtime_registry(
        self,
        base_registry: Mapping[str, ToolDescriptor],
        handlers: Mapping[str, Callable],
    ) -> dict[str, ToolDescriptor]:
        """基于当前运行状态补齐动态工具描述符。

        Args:
            base_registry: 基础工具注册表。
            handlers: 用于绑定动态工具的处理函数映射。

        Returns:
            组合完成的最终工具注册表。
        """
        return self.registry_builder.build_extended_registry(base_registry, handlers)

    def execute_tool(self, request: ToolExecutionRequest, registry: Mapping[str, ToolDescriptor]) -> ToolExecutionResult:
        """执行标准的工具调用请求。

        Args:
            request: 标准请求契约。
            registry: 工具注册表。

        Returns:
            标准化的工具执行结果。
        """
        return self.local_executor.execute(
            tool_registry=registry,
            name=request.tool_name,
            arguments=request.arguments,
            context=request.context,
        )

    def execute_tool_streaming(
        self, request: ToolExecutionRequest, registry: Mapping[str, ToolDescriptor]
    ) -> Iterator[ToolStreamUpdate]:
        """执行流式工具调用请求。

        Args:
            request: 标准请求契约。
            registry: 工具注册表。

        Returns:
            流式事件更新序列。
        """
        yield from self.local_executor.execute_streaming(
            tool_registry=registry,
            name=request.tool_name,
            arguments=request.arguments,
            context=request.context,
        )

    def list_mcp_resources(self, query: McpResourceQuery) -> tuple[JSONDict, ...]:
        """列出环境内可用的 MCP 资源。

        Args:
            query: 资源过滤参数契约。

        Returns:
            转换为标准字典的资源列表。
        """
        return self.mcp_adapter.list_resources(query)

    def search_mcp_capabilities(self, query: McpCapabilityQuery) -> tuple[JSONDict, ...]:
        """搜索远端 MCP 能力目录。

        Args:
            query: 能力检索参数契约。

        Returns:
            转换为标准字典的能力目录项。
        """
        return self.mcp_adapter.search_capabilities(query)
