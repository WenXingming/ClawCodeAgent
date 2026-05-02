"""tools 工具模块公共导出。

严格遵循由外向内设计，外部编排器仅允许通过 ToolsGateway 门面、
ToolsGatewayFactory 工厂，以及 core_contracts 中的纯数据契约与本模块交互。
"""

from __future__ import annotations

from dataclasses import dataclass

from core_contracts.tools_contracts import ToolDescriptor, ToolRegistry
from tools.local.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutor
from tools.local.filesystem_tools import FileSystemToolProvider
from tools.local.shell_tools import ShellToolProvider
from tools.mcp_adapter import McpOperationsAdapter, McpRuntimeProvider
from tools.registry_builder import DynamicRegistryBuilder, WorkspaceGatewayProvider
from tools.tools_gateway import ToolsGateway


@dataclass(frozen=True)
class ToolsGatewayFactory:
    """ToolsGateway 的标准装配工厂。"""

    @staticmethod
    def create_gateway(
        workspace_gateway: WorkspaceGatewayProvider, mcp_runtime: McpRuntimeProvider
    ) -> ToolsGateway:
        """基于当前上下文依赖组装并返回 Tools Gateway 实例。
        Args:
            workspace_gateway (Any): 上层传入的工作区网关
            mcp_runtime (Any): 上层传入的 MCP 运行时
        Returns:
            ToolsGateway: 装配完毕的顶级门面
        """
        return ToolsGateway(
            local_executor=ToolExecutor(),
            registry_builder=DynamicRegistryBuilder(workspace_gateway=workspace_gateway),
            mcp_adapter=McpOperationsAdapter(mcp_runtime=mcp_runtime),
            tool_registry=ToolsGatewayFactory.create_default_registry(ShellSecurityPolicy()),
        )

    @staticmethod
    def create_default_registry(shell_security_policy: ShellSecurityPolicy) -> ToolRegistry:
        """组装内置的本地基础工具注册表。
        Args:
            shell_security_policy (ShellSecurityPolicy): Shell 命令安全策略实例
        Returns:
            ToolRegistry: 包含基础工具的注册表对象
        """
        tools = list(FileSystemToolProvider().build_tools())
        tools.append(ShellToolProvider(shell_security_policy).build_tool())
        return ToolRegistry.from_tools(*tools)


__all__ = [
    'ToolsGateway',
    'ToolsGatewayFactory',
]
