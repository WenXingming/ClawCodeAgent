"""工具执行上下文与执行器。

提供 ToolExecutor 作为工具调用的统一执行器，封装工具注册表查询、
参数验证、错误包装和流式调用的分发逻辑（含 execute_call 的回调合并）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

from core_contracts.config import ExecutionPolicy, WorkspaceScope
from core_contracts.errors import GatewayError, GatewayPermissionError, GatewayRuntimeError
from core_contracts.messaging import ToolExecutionResult
from core_contracts.primitives import JSONDict
from core_contracts.tools_contracts import (
    ToolPermissionPolicy,
    ToolDescriptor,
    ToolExecutionContext,
    ToolRegistry,
    ToolStreamUpdate,
)


class ToolPermissionError(GatewayPermissionError):
    """表示工具调用被权限策略拒绝。"""

    pass


class ToolExecutionError(GatewayRuntimeError):
    """表示工具参数非法或执行过程失败。"""

    pass


@dataclass(frozen=True)
class ToolExecutor:
    """封装工具执行与错误包装。

    该类无状态字段，所有方法均为纯委托或错误包装逻辑，可作为共享实例复用。
    """

    # ── 公有方法 ──────────────────────────────────────────────────

    def build_context(
        self,
        workspace_scope: WorkspaceScope,
        execution_policy: ExecutionPolicy,
        permissions: ToolPermissionPolicy,
        *,
        tool_registry: ToolRegistry | None = None,
        safe_env: dict[str, str] | None = None,
    ) -> ToolExecutionContext:
        """根据运行时配置构造工具执行上下文。"""
        return ToolExecutionContext.build(
            workspace_scope,
            execution_policy,
            permissions,
            tool_registry=tool_registry,
            safe_env=safe_env,
        )

    def execute(
        self,
        tool_registry: ToolRegistry,
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
        tool_registry: ToolRegistry,
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

    def execute_call(
        self,
        tool_registry: ToolRegistry,
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
        *,
        on_stream_update: Callable[[ToolStreamUpdate], None] | None = None,
    ) -> ToolExecutionResult:
        """执行一次工具调用，并在流式片段出现时回调上报。"""
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

    # ── 私有方法（按公有方法首次调用顺序深度优先排列） ──────────────

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
