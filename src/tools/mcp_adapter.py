"""MCP 运行时的防腐层适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core_contracts.primitives import JSONDict
from core_contracts.tools_contracts import McpCapabilityQuery, McpResourceQuery, ToolsGatewayError


class McpRuntimeProvider(Protocol):
    """描述 McpOperationsAdapter 对底层 MCP Runtime 的行为需求。"""

    def list_resources(self, query: str, server_name: str | None, limit: int | None) -> list: ...
    def search_capabilities(self, query: str, server_name: str | None, limit: int | None) -> list: ...


@dataclass(frozen=True)
class McpOperationsAdapter:
    """包装原始 MCP Runtime 的适配器，防止底层异常和对象向上层泄漏。"""

    mcp_runtime: McpRuntimeProvider  # McpRuntimeProvider: 注入的外部 MCP Runtime 实例。

    def list_resources(self, query: McpResourceQuery) -> tuple[JSONDict, ...]:
        """查询并返回纯数据结构的 MCP 资源列表。

        Args:
            query: 规范查询 DTO。

        Returns:
            转换后的字典对象序列。

        Raises:
            ToolsGatewayError: 检索或传输失败时抛出。
        """
        try:
            resources = self.mcp_runtime.list_resources(
                query=query.query,
                server_name=query.server_name,
                limit=query.limit,
            )
            return tuple(resource.to_dict() for resource in resources)
        except Exception as e:
            raise ToolsGatewayError(f"MCP resource query failed: {str(e)}") from e

    def search_capabilities(self, query: McpCapabilityQuery) -> tuple[JSONDict, ...]:
        """查询并返回纯数据结构的 MCP 能力目录。

        Args:
            query: 能力查询 DTO。

        Returns:
            转换后的字典对象序列。

        Raises:
            ToolsGatewayError: 检索或传输失败时抛出。
        """
        try:
            capabilities = self.mcp_runtime.search_capabilities(
                query=query.query,
                server_name=query.server_name,
                limit=query.limit,
            )
            return tuple(cap.to_dict() for cap in capabilities)
        except Exception as e:
            raise ToolsGatewayError(f"MCP capability search failed: {str(e)}") from e
