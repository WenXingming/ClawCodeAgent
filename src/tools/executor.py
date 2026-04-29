"""工具执行上下文与执行器。

提供 ToolExecutor 作为工具调用的统一执行器，封装工具注册表查询、
参数验证、错误包装和流式调用的分发逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Mapping

from core_contracts.gateway_errors import GatewayError, GatewayPermissionError, GatewayRuntimeError
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.protocol import JSONDict, ToolExecutionResult
from core_contracts.runtime_policy import ExecutionPolicy, WorkspaceScope
from core_contracts.tools_contracts import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolStreamUpdate,
    build_execution_context,
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

    # ── 上下文构建 ───────────────────────────────────────────────────

    def build_context(
        self,
        workspace_scope: WorkspaceScope,
        execution_policy: ExecutionPolicy,
        permissions: ToolPermissionPolicy,
        *,
        tool_registry: Mapping[str, ToolDescriptor] | None = None,
        safe_env: dict[str, str] | None = None,
    ) -> ToolExecutionContext:
        """根据运行时配置构造工具执行上下文。
        Args:
            workspace_scope (WorkspaceScope): 工作区路径与运行目录约束。
            execution_policy (ExecutionPolicy): 执行超时与输出预算配置。
            permissions (ToolPermissionPolicy): 工具权限策略。
            tool_registry (Mapping[str, ToolDescriptor] | None): 可选工具注册表。
            safe_env (dict[str, str] | None): 可选安全环境变量覆盖。
        Returns:
            ToolExecutionContext: 供工具处理器消费的不可变上下文。
        """
        return build_execution_context(
            workspace_scope,
            execution_policy,
            permissions,
            tool_registry=tool_registry,
            safe_env=safe_env,
        )

    # ── 普通执行 ────────────────────────────────────────────────────

    def execute(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """按工具名执行一次普通工具调用。
        Args:
            tool_registry (Mapping[str, ToolDescriptor]): 当前可见工具注册表。
            name (str): 目标工具名。
            arguments (JSONDict): 工具参数对象。
            context (ToolExecutionContext): 工具执行上下文。
        Returns:
            ToolExecutionResult: 标准化的执行结果。
        """
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

    @staticmethod
    def _success_result(
        name: str,
        payload: str | tuple[str, JSONDict],
    ) -> ToolExecutionResult:
        """把 handler 返回值封装为统一工具结果。
        Args:
            name (str): 工具名称。
            payload (str | tuple[str, JSONDict]): handler 返回的文本或(文本, 元数据)元组。
        Returns:
            ToolExecutionResult: 成功执行的结构化结果。
        """
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
        """构造统一的失败工具结果。
        Args:
            name (str): 工具名称。
            exc (BaseException): 被捕获的异常对象。
            error_kind (str): 语义化的错误类别标签。
        Returns:
            ToolExecutionResult: 失败执行的结构化结果。
        """
        return ToolExecutionResult(
            name=name,
            ok=False,
            content=str(exc),
            metadata={'error_kind': error_kind},
        )

    # ── 流式执行 ────────────────────────────────────────────────────

    def execute_streaming(
        self,
        tool_registry: Mapping[str, ToolDescriptor],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """按工具名执行一次流式工具调用。
        Args:
            tool_registry (Mapping[str, ToolDescriptor]): 当前可见工具注册表。
            name (str): 目标工具名。
            arguments (JSONDict): 工具参数对象。
            context (ToolExecutionContext): 工具执行上下文。
        Returns:
            Iterator[ToolStreamUpdate]: 逐步产出的流式更新序列。
        """
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


def _unknown_tool_result(name: str) -> ToolExecutionResult:
    """为未知工具返回统一的结构化错误结果。
    Args:
        name (str): 无法识别的工具名称。
    Returns:
        ToolExecutionResult: 携带 unknown_tool 标记的失败结果。
    """
    return ToolExecutionResult(
        name=name,
        ok=False,
        content=f'Unknown tool: {name}',
        metadata={'error_kind': 'unknown_tool'},
    )
