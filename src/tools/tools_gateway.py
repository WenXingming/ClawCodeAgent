"""tools 领域统一网关。

该文件是 tools 子系统对外唯一边界，提供极致清爽的 Facade 模式。
所有复杂业务均委托给注入的内部组件（Adapter, Builder, Executor）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator, Mapping

from core_contracts.config import ExecutionPolicy, WorkspaceScope
from core_contracts.messaging import ToolExecutionResult
from core_contracts.primitives import JSONDict
from core_contracts.tools_contracts import (
    McpCapabilityQuery,
    McpResourceQuery,
    ToolExecutionContext,
    ToolPermissionPolicy,
    ToolExecutionRequest,
    ToolRegistry,
    ToolStreamUpdate,
)
from tools.executor import ToolExecutor
from tools.mcp_adapter import McpOperationsAdapter
from tools.registry_builder import DynamicRegistryBuilder


@dataclass
class ToolsGateway:
    """tools 领域唯一对外入口。"""

    local_executor: ToolExecutor
    registry_builder: DynamicRegistryBuilder
    mcp_adapter: McpOperationsAdapter
    tool_registry: ToolRegistry

    def extend_runtime_registry(
        self,
        handlers: Mapping[str, Callable],
    ) -> ToolRegistry:
        """基于当前运行状态补齐动态工具描述符。

        Args:
            handlers: 用于绑定动态工具的处理函数映射。

        Returns:
            组合完成的最终工具注册表。
        """
        self.tool_registry = self.registry_builder.build_extended_registry(self.tool_registry, handlers)
        return self.tool_registry

    def to_openai_tools(self) -> list[JSONDict]:
        """返回当前工具注册表对应的 OpenAI 兼容 schema 列表。"""
        return self.tool_registry.to_openai_tools()

    def build_execution_context(
        self,
        workspace_scope: WorkspaceScope,
        execution_policy: ExecutionPolicy,
        permissions: ToolPermissionPolicy,
        *,
        safe_env: dict[str, str] | None = None,
    ) -> ToolExecutionContext:
        """按当前网关状态构造工具执行上下文。"""
        return ToolExecutionContext.build(
            workspace_scope,
            execution_policy,
            permissions,
            tool_registry=self.tool_registry,
            safe_env=safe_env,
        )

    def execute_tool(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        """执行标准的工具调用请求。

        Args:
            request: 标准请求契约。
        Returns:
            标准化的工具执行结果。
        """
        return self.local_executor.execute(
            tool_registry=self.tool_registry,
            name=request.tool_name,
            arguments=request.arguments,
            context=request.context,
        )

    def execute_tool_streaming(
        self, request: ToolExecutionRequest
    ) -> Iterator[ToolStreamUpdate]:
        """执行流式工具调用请求。

        Args:
            request: 标准请求契约。
        Returns:
            流式事件更新序列。
        """
        yield from self.local_executor.execute_streaming(
            tool_registry=self.tool_registry,
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
