"""工具执行上下文与执行器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Mapping

from core_contracts.gateway_errors import GatewayError, GatewayPermissionError, GatewayRuntimeError
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict, ToolExecutionResult
from core_contracts.runtime_policy import ExecutionPolicy, WorkspaceScope
from core_contracts.tools_contracts import ToolDescriptor, ToolExecutionContext, ToolStreamUpdate, build_execution_context


class ToolPermissionError(GatewayPermissionError):
    """表示工具调用被权限策略拒绝。"""


class ToolExecutionError(GatewayRuntimeError):
    """表示工具参数非法或执行过程失败。"""


@dataclass(frozen=True)
class ToolExecutor:
    """封装工具执行与错误包装。"""

    def build_context(
        self,
        workspace_scope: WorkspaceScope,
        execution_policy: ExecutionPolicy,
        permissions: ToolPermissionPolicy,
        *,
        tool_registry: Mapping[str, ToolDescriptor] | None = None,
        safe_env: dict[str, str] | None = None,
    ) -> ToolExecutionContext:
        """根据运行时配置构造工具执行上下文。"""
        return build_execution_context(
            workspace_scope,
            execution_policy,
            permissions,
            tool_registry=tool_registry,
            safe_env=safe_env,
        )

    def execute(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """按工具名执行一次普通工具调用。"""
        tool = tool_registry.get(name)
        if tool is None:
            return _unknown_tool_result(name)

        try:
            payload = tool.handler(arguments, context)
        except (ToolPermissionError, GatewayPermissionError) as exc:
            return self._failure_result(name, exc, error_kind='permission_denied')
        except (ToolExecutionError, GatewayRuntimeError, GatewayError, OSError, UnicodeError) as exc:
            return self._failure_result(name, exc, error_kind='tool_execution_error')

        return self._success_result(name, payload)

    def execute_streaming(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """按工具名执行一次流式工具调用。"""
        tool = tool_registry.get(name)
        if tool is None:
            yield ToolStreamUpdate(kind='result', result=_unknown_tool_result(name))
            return

        if tool.stream_handler is None:
            yield ToolStreamUpdate(kind='result', result=self.execute(tool_registry, name, arguments, context))
            return

        try:
            yield from tool.stream_handler(arguments, context)
        except (ToolPermissionError, GatewayPermissionError) as exc:
            yield ToolStreamUpdate(
                kind='result',
                result=self._failure_result(name, exc, error_kind='permission_denied'),
            )
        except (ToolExecutionError, GatewayRuntimeError, GatewayError, OSError, UnicodeError) as exc:
            yield ToolStreamUpdate(
                kind='result',
                result=self._failure_result(name, exc, error_kind='tool_execution_error'),
            )

    @staticmethod
    def _success_result(
        name: str,
        payload: str | tuple[str, JSONDict],
    ) -> ToolExecutionResult:
        """把 handler 返回值封装为统一工具结果。"""
        if isinstance(payload, tuple):
            content, metadata = payload
        else:
            content, metadata = payload, {}
        return ToolExecutionResult(
            name=name,
            ok=True,
            content=content,
            metadata=metadata,
        )

    @staticmethod
    def _failure_result(
        name: str,
        exc: BaseException,
        *,
        error_kind: str,
    ) -> ToolExecutionResult:
        """构造统一的失败工具结果。"""
        return ToolExecutionResult(
            name=name,
            ok=False,
            content=str(exc),
            metadata={'error_kind': error_kind},
        )


def _unknown_tool_result(name: str) -> ToolExecutionResult:
    """为未知工具返回统一的结构化错误结果。"""
    return ToolExecutionResult(
        name=name,
        ok=False,
        content=f'Unknown tool: {name}',
        metadata={'error_kind': 'unknown_tool'},
    )