"""Agent 领域最小闭环编排器。

该文件是 agent 模块的唯一边界，实现 ReAct 风格的工具调用循环。
AgentGateway 作为门面，仅负责依赖注入分发与循环调度，
不包含任何模型调用或工具执行的具体逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from core_contracts.config import (
    BudgetConfig,
    ExecutionPolicy,
    WorkspaceScope,
)
from core_contracts.messaging import ToolCall, ToolExecutionResult
from core_contracts.model import ModelClient, ModelConfig
from core_contracts.outcomes import AgentRunResult
from core_contracts.primitives import TokenUsage
from core_contracts.session_contracts import AgentSessionSnapshot, AgentSessionState
from core_contracts.tools_contracts import ToolExecutionContext, ToolExecutionRequest, ToolPermissionPolicy
from tools import ToolsGateway


@dataclass
class AgentGateway:
    """Agent 领域唯一对外入口，编排模型调用与工具执行的闭环循环。

    所有依赖均通过构造函数注入，不包含任何硬编码的外部引用。
    """

    # ── 注入依赖 ──────────────────────────────────────────────────────────

    # 各个模块的 Gateway 或 Facade 实例，满足 Agent 运行所需的外部能力。
    tools_gateway: ToolsGateway  # ToolsGateway: 注入的 tools 域门面，Agent 仅通过该门面访问工具相关能力。

    client: ModelClient  # ModelClient: 注入的大模型客户端，满足 ModelClient Protocol。
    system_prompt: str  # str: 注入的系统提示词，作为每轮对话的顶层指令。
    workspace_scope: WorkspaceScope  # WorkspaceScope: 注入的工作区范围配置。
    execution_policy: ExecutionPolicy  # ExecutionPolicy: 注入的执行约束配置。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy: 注入的工具权限配置。
    budget_config: BudgetConfig | None = None  # BudgetConfig | None: 注入的预算配置。
    model_config: ModelConfig | None = None  # ModelConfig | None: 注入的模型配置。
    max_tool_turns: int = 25  # int: 单次运行中允许的最大工具调用轮数。

    # ── 公有方法 ──────────────────────────────────────────────────────────

    def run(self, prompt: str) -> AgentRunResult:
        """启动一次全新的 Agent 会话运行。

        Args:
            prompt: 用户输入文本。

        Returns:
            包含最终输出、轮次统计和 token 用量的运行结果。
        """
        session_id = uuid4().hex
        session_state = AgentSessionState.create(prompt)
        session_state.messages.insert(0, {'role': 'system', 'content': self.system_prompt})
        return self._run_loop(prompt=prompt, session_id=session_id, session_state=session_state)

    def resume(self, prompt: str, session_snapshot: AgentSessionSnapshot) -> AgentRunResult:
        """从已有快照恢复并继续 Agent 会话运行。

        Args:
            prompt: 新的用户输入文本。
            session_snapshot: 待恢复的持久化会话快照。

        Returns:
            包含最终输出、轮次统计和 token 用量的运行结果。
        """
        session_state = AgentSessionState.from_persisted(
            list(session_snapshot.messages),
            list(session_snapshot.transcript),
        )
        session_state.append_user(prompt)
        return self._run_loop(
            prompt=prompt,
            session_id=session_snapshot.session_id,
            session_state=session_state,
        )

    # ── 私有方法（深度优先调用链顺序） ─────────────────────────────────────

    def _run_loop(
        self,
        *,
        prompt: str,
        session_id: str,
        session_state: AgentSessionState,
    ) -> AgentRunResult:
        """核心工具调用循环。

        循环调用模型并执行工具，直到模型返回纯文本或达到最大轮数。

        Args:
            prompt: 用户输入文本。
            session_id: 当前运行的会话 ID。
            session_state: 维护消息历史的可变会话状态。

        Returns:
            聚合后的端到端运行结果。
        """

        execution_context = self.tools_gateway.build_execution_context(
            self.workspace_scope,
            self.execution_policy,
            self.permissions,
        ) # 构建注入给工具的执行上下文
        tools_schema = self.tools_gateway.to_openai_tools()

        total_usage = TokenUsage()
        total_tool_calls = 0
        final_text = ''
        stop_reason: str | None = None
        turn = 0

        for turn in range(self.max_tool_turns):
            try:
                response = self.client.complete(
                    messages=session_state.to_messages(),
                    tools=tools_schema if tools_schema else None,
                )
            except Exception as exc:
                stop_reason = f'model_error: {exc}'
                break

            session_state.append_assistant_turn(response)
            total_usage = total_usage + response.usage

            if not response.tool_calls:
                final_text = response.content
                stop_reason = response.finish_reason or 'stop'
                break

            tool_results = self._execute_tool_calls(response.tool_calls, execution_context)
            for call, result in zip(response.tool_calls, tool_results):
                session_state.append_tool_result(call, result)
            total_tool_calls += len(response.tool_calls)
        else:
            stop_reason = f'max_tool_turns_reached: {self.max_tool_turns}'

        if self.model_config and self.model_config.pricing:
            cost = self.model_config.pricing.estimate_cost_usd(total_usage)
        else:
            cost = 0.0

        return AgentRunResult(
            final_output=final_text,
            turns=turn + 1,
            tool_calls=total_tool_calls,
            transcript=session_state.transcript(),
            usage=total_usage,
            total_cost_usd=cost,
            stop_reason=stop_reason,
            session_id=session_id,
        )

    def _execute_tool_calls(
        self,
        tool_calls: tuple[ToolCall, ...],
        execution_context: ToolExecutionContext,
    ) -> list[ToolExecutionResult]:
        """批量执行模型请求的工具调用。

        单个工具执行失败不会中断整个批次——错误会被包装为
        ToolExecutionResult(ok=False) 返回给模型继续处理。

        Args:
            tool_calls: 模型返回的工具调用序列。
            execution_context: 注入给每个工具调用的执行上下文。

        Returns:
            与输入 tool_calls 一一对应的工具执行结果列表。
        """
        results: list[ToolExecutionResult] = []
        for call in tool_calls:
            request = ToolExecutionRequest(
                tool_name=call.name,
                arguments=call.arguments,
                context=execution_context,
            )
            try:
                result = self.tools_gateway.execute_tool(request)
            except Exception as exc:
                result = ToolExecutionResult(
                    name=call.name,
                    ok=False,
                    content=f'Tool execution error: {exc}',
                )
            results.append(result)
        return results
