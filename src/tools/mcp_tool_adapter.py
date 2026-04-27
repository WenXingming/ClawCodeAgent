"""MCP 工具适配层。

该模块负责把远端 MCP server 暴露的工具定义展开成 Agent 顶层可见的
本地工具模式，并在真正执行时把调用再转发回 MCPRuntime。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core_contracts.protocol import JSONDict
from tools.agent_tools import AgentTool, ToolExecutionContext, ToolExecutionError, ToolPermissionError

from .mcp_models import MCPTool, MCPTransportError
from .mcp_runtime import MCPRuntime


_FILESYSTEM_WRITE_TOOL_NAMES = frozenset({'write_file', 'edit_file', 'create_directory', 'move_file'})


@dataclass(frozen=True)
class MCPToolAdapter:
    """把远端 MCP 工具展开为 Agent 可直接调用的工具。

    外部通常先注入一个已经完成 manifest 发现的 MCPRuntime，再调用
    build_tools 生成一组 AgentTool。每个导出的工具会在运行时执行权限
    检查，然后通过运行时回调远端 MCP server。
    """

    runtime: MCPRuntime  # MCPRuntime: 远端工具发现与调用入口。

    def build_tools(self, occupied_registry: Mapping[str, AgentTool]) -> dict[str, AgentTool]:
        """生成可挂入本地工具注册表的 MCP 工具映射。

        Args:
            occupied_registry (Mapping[str, AgentTool]): 当前已占用的工具名映射，用于避免命名冲突。
        Returns:
            dict[str, AgentTool]: 以导出工具名为键的 AgentTool 映射。
        Raises:
            ValueError: 当工具名冲突无法通过前缀消解时抛出。
        """
        expanded: dict[str, AgentTool] = {}
        occupied_names = set(occupied_registry)

        for remote_tool in self.runtime.list_tools():
            exported_name = self._resolve_exported_name(remote_tool, occupied_names)
            expanded[exported_name] = self._build_tool(remote_tool, exported_name=exported_name)
            occupied_names.add(exported_name)

        return expanded

    def _resolve_exported_name(self, remote_tool: MCPTool, occupied_names: set[str]) -> str:
        """为远端工具生成对外暴露的唯一名称。

        Args:
            remote_tool (MCPTool): 待展开的远端工具定义。
            occupied_names (set[str]): 当前已经被占用的工具名称集合。
        Returns:
            str: 可安全导出的工具名称。
        Raises:
            ValueError: 当普通名和带 server 前缀的名称都已冲突时抛出。
        """
        candidate = remote_tool.name
        if candidate not in occupied_names:
            return candidate

        prefixed = f'mcp_{remote_tool.server_name}_{remote_tool.name}'
        if prefixed in occupied_names:
            raise ValueError(f'Expanded MCP tool name conflict: {prefixed!r}')
        return prefixed

    def _build_tool(self, remote_tool: MCPTool, *, exported_name: str) -> AgentTool:
        """把单个 MCPTool 包装为 AgentTool。

        Args:
            remote_tool (MCPTool): 远端 MCP 工具定义。
            exported_name (str): 对外暴露给模型的工具名称。
        Returns:
            AgentTool: 可直接注册到本地注册表的工具对象。
        """
        parameters = dict(remote_tool.input_schema) if remote_tool.input_schema else {
            'type': 'object',
            'properties': {},
            'additionalProperties': True,
        }
        description = remote_tool.description or (
            f'Expanded MCP tool {remote_tool.name} from server {remote_tool.server_name}.'
        )

        def _handler(arguments: JSONDict, context: ToolExecutionContext):
            return self._execute_tool(
                exported_name=exported_name,
                remote_tool=remote_tool,
                arguments=arguments,
                context=context,
            )

        return AgentTool(
            name=exported_name,
            description=description,
            parameters=parameters,
            handler=_handler,
        )

    def _execute_tool(
        self,
        *,
        exported_name: str,
        remote_tool: MCPTool,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> str | tuple[str, JSONDict]:
        """执行展开后的 MCP 工具并标准化返回内容。

        Args:
            exported_name (str): 对外暴露的工具名称。
            remote_tool (MCPTool): 实际要调用的远端工具定义。
            arguments (JSONDict): 模型传入的 JSON 参数对象。
            context (ToolExecutionContext): 当前工具调用上下文。
        Returns:
            str | tuple[str, JSONDict]: 文本结果，或带附加元数据的结果元组。
        Raises:
            ToolPermissionError: 当工具触发写文件权限限制时抛出。
            ToolExecutionError: 当参数格式错误或远端调用失败时抛出。
        """
        self._ensure_tool_allowed(remote_tool, context)
        if not isinstance(arguments, dict):
            raise ToolExecutionError('Expanded MCP tool arguments must be a JSON object')

        try:
            result = self.runtime.call_tool(
                remote_tool.name,
                arguments=dict(arguments),
                server_name=remote_tool.server_name,
                max_chars=context.max_output_chars,
            )
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        content = _truncate_tool_output(
            self.runtime.render_tool_result(result),
            context.max_output_chars,
        )
        return (
            content,
            {
                'exported_name': exported_name,
                'server_name': result.server_name,
                'tool_name': result.tool_name,
                'is_error': result.is_error,
            },
        )

    @staticmethod
    def _ensure_tool_allowed(remote_tool: MCPTool, context: ToolExecutionContext) -> None:
        """检查远端工具是否越过当前会话权限边界。

        Args:
            remote_tool (MCPTool): 即将执行的远端工具定义。
            context (ToolExecutionContext): 当前工具调用上下文。
        Returns:
            None: 无返回值。
        Raises:
            ToolPermissionError: 当 filesystem server 的写入类工具在只读权限下被调用时抛出。
        """
        if remote_tool.server_name == 'filesystem' and remote_tool.name in _FILESYSTEM_WRITE_TOOL_NAMES:
            if not context.permissions.allow_file_write:
                raise ToolPermissionError('File write permission denied: allow_file_write=false')


def _truncate_tool_output(content: str, max_chars: int) -> str:
    """按上限裁剪工具输出，并保留被裁剪提示。

    Args:
        content (str): 原始文本内容。
        max_chars (int): 允许返回的最大字符数。
    Returns:
        str: 裁剪后的文本结果。
    """
    if len(content) <= max_chars:
        return content
    omitted = len(content) - max_chars
    suffix = f'\n\n... truncated {omitted} characters'
    keep = max(0, max_chars - len(suffix))
    return content[:keep] + suffix