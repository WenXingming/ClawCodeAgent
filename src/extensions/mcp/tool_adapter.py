"""Adapter that expands remote MCP tools into top-level agent tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core_contracts.protocol import JSONDict
from tools.agent_tools import AgentTool, ToolExecutionContext, ToolExecutionError, ToolPermissionError

from .runtime import MCPRuntime, MCPTool, MCPTransportError


_FILESYSTEM_WRITE_TOOL_NAMES = frozenset({'write_file', 'edit_file', 'create_directory', 'move_file'})


@dataclass(frozen=True)
class MCPToolAdapter:
    """Expand transport-backed MCP tools into model-visible top-level tools."""

    runtime: MCPRuntime

    def build_tools(self, occupied_registry: Mapping[str, AgentTool]) -> dict[str, AgentTool]:
        """Build expanded MCP tools while preserving occupied local names."""
        expanded: dict[str, AgentTool] = {}
        occupied_names = set(occupied_registry)

        for remote_tool in self.runtime.list_tools():
            exported_name = self._resolve_exported_name(remote_tool, occupied_names)
            expanded[exported_name] = self._build_tool(remote_tool, exported_name=exported_name)
            occupied_names.add(exported_name)

        return expanded

    def _resolve_exported_name(self, remote_tool: MCPTool, occupied_names: set[str]) -> str:
        candidate = remote_tool.name
        if candidate not in occupied_names:
            return candidate

        prefixed = f'mcp_{remote_tool.server_name}_{remote_tool.name}'
        if prefixed in occupied_names:
            raise ValueError(f'Expanded MCP tool name conflict: {prefixed!r}')
        return prefixed

    def _build_tool(self, remote_tool: MCPTool, *, exported_name: str) -> AgentTool:
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
        if remote_tool.server_name == 'filesystem' and remote_tool.name in _FILESYSTEM_WRITE_TOOL_NAMES:
            if not context.permissions.allow_file_write:
                raise ToolPermissionError('File write permission denied: allow_file_write=false')


def _truncate_tool_output(content: str, max_chars: int) -> str:
    """Truncate rendered MCP output to fit the agent tool output budget."""
    if len(content) <= max_chars:
        return content
    omitted = len(content) - max_chars
    suffix = f'\n\n... truncated {omitted} characters'
    keep = max(0, max_chars - len(suffix))
    return content[:keep] + suffix