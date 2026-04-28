"""工具子系统门面。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator, Mapping

from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict, ToolExecutionResult
from core_contracts.runtime_policy import ExecutionPolicy, WorkspaceScope
from tools.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutionContext, ToolExecutor, ToolStreamUpdate
from tools.local.filesystem_tools import build_filesystem_tools
from tools.local.shell_tools import build_shell_tool
from tools.registry import LocalTool, build_registry, render_openai_tools


@dataclass(frozen=True)
class ToolGateway:
    """统一暴露工具注册、上下文构造与执行入口。"""

    shell_security_policy: ShellSecurityPolicy = field(default_factory=ShellSecurityPolicy)
    _executor: ToolExecutor = field(default_factory=ToolExecutor)

    def default_registry(self) -> dict[str, LocalTool]:
        """返回内置基础工具注册表。"""
        return build_registry(
            *build_filesystem_tools(),
            build_shell_tool(self.shell_security_policy),
        )

    def build_context(
        self,
        workspace_scope: WorkspaceScope,
        execution_policy: ExecutionPolicy,
        permissions: ToolPermissionPolicy,
        *,
        tool_registry: Mapping[str, LocalTool] | None = None,
        safe_env: dict[str, str] | None = None,
    ) -> ToolExecutionContext:
        """根据运行时配置构造工具执行上下文。"""
        return self._executor.build_context(
            workspace_scope,
            execution_policy,
            permissions,
            tool_registry=tool_registry,
            safe_env=safe_env,
        )

    def execute(
        self,
        tool_registry: Mapping[str, LocalTool],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """执行一次工具调用。"""
        return self._executor.execute(tool_registry, name, arguments, context)

    def execute_streaming(
        self,
        tool_registry: Mapping[str, LocalTool],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """执行一次流式工具调用。"""
        return self._executor.execute_streaming(tool_registry, name, arguments, context)

    def execute_call(
        self,
        tool_registry: Mapping[str, LocalTool],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
        *,
        on_stream_update: Callable[[ToolStreamUpdate], None] | None = None,
    ) -> ToolExecutionResult:
        """执行一次工具调用，并在有流式片段时回调上报。"""
        final_result: ToolExecutionResult | None = None
        for update in self.execute_streaming(tool_registry, name, arguments, context):
            if update.kind == 'result':
                final_result = update.result
                continue
            if on_stream_update is not None:
                on_stream_update(update)

        if final_result is not None:
            return final_result
        return ToolExecutionResult(
            name=name,
            ok=False,
            content='Streaming tool execution returned no final result.',
            metadata={'error_kind': 'tool_execution_error'},
        )

    def to_openai_tools(self, tool_registry: Mapping[str, LocalTool]) -> list[JSONDict]:
        """把工具注册表投影为模型可见 schema 列表。"""
        return render_openai_tools(tool_registry)