"""LocalAgent 最小闭环实现。

本模块实现最小 run 主循环：
1) 调模型。
2) 执行工具并回填。
3) 达到停止条件后返回 AgentRunResult。

ISSUE-008 扩展：
4) resume(prompt, session_snapshot) 从持久化会话恢复并继续执行。
    - 严格继承 session_snapshot 的 model/runtime 配置。
   - usage/cost/turns/tool_calls 从历史基线累计。
   - session_id 保持不变。
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field
from uuid import uuid4

from context.context_budget_evaluator import ContextBudgetEvaluator
from budget.budget_guard import BudgetGuard
from interface.slash_commands_interface import SlashCommandContext, SlashCommandDispatcher, SlashCommandResult
from context.context_compactor import ContextCompactor
from orchestration.budget_context_orchestrator import BudgetContextOrchestrator
from context.context_snipper import ContextSnipper
from core_contracts.config import AgentRuntimeConfig
from core_contracts.protocol import JSONDict, OneTurnResponse, ToolCall, ToolExecutionResult
from core_contracts.result import AgentRunResult
from core_contracts.usage import TokenUsage
from extensions.mcp_runtime import MCPRuntime, MCPTransportError
from extensions.search_runtime import SearchQueryError, SearchRuntime
from openai_client.openai_client import OpenAIClient
from extensions.hook_policy_runtime import HookPolicyRuntime
from extensions.plugin_runtime import PluginRuntime
from session.session_snapshot import AgentSessionSnapshot
from session.session_state import AgentSessionState
from session.session_store import AgentSessionStore
from tools.agent_tools import AgentTool, ToolExecutionError, build_tool_context, default_tool_registry, execute_tool


@dataclass
class LocalAgent:
    """最小可用的本地编码代理。"""

    client: OpenAIClient  # 模型客户端。
    runtime_config: AgentRuntimeConfig  # 运行配置。
    session_store: AgentSessionStore  # 会话持久化依赖。
    tool_registry: dict[str, AgentTool] = field(default_factory=default_tool_registry)  # 可用工具集合。
    budget_evaluator: ContextBudgetEvaluator = field(default_factory=ContextBudgetEvaluator)
    context_snipper: ContextSnipper = field(default_factory=ContextSnipper)
    context_compactor: ContextCompactor = field(init=False)
    budget_context_orchestrator: BudgetContextOrchestrator = field(init=False)
    search_runtime: SearchRuntime = field(init=False)
    mcp_runtime: MCPRuntime = field(init=False)
    plugin_runtime: PluginRuntime = field(init=False)
    hook_policy_runtime: HookPolicyRuntime = field(init=False)
    slash_dispatcher: SlashCommandDispatcher = field(init=False)

    def run(self, prompt: str) -> AgentRunResult:
        """执行一轮端到端任务（新会话）。

        Args:
            prompt (str): 用户输入的本轮任务提示词。

        Returns:
            AgentRunResult: 本轮运行结果（含输出、事件、用量与会话信息）。
        """
        session_state = AgentSessionState()
        session_id = uuid4().hex
        local_result = self._prepare_prompt(
            prompt=prompt,
            session_state=session_state,
            session_id=session_id,
            turns_offset=0,
            usage_baseline=TokenUsage(),
            cost_baseline=0.0,
        )
        if local_result is not None:
            return local_result
        return self._execute_loop(
            session_state=session_state,
            session_id=session_id,
            turns_offset=0,
            usage_baseline=TokenUsage(),
            cost_baseline=0.0,
        )

    def resume(self, prompt: str, session_snapshot: AgentSessionSnapshot) -> AgentRunResult:
        """从已保存的会话恢复并继续执行新 prompt。

        严格继承 session_snapshot 的 model/runtime 配置；
        usage、turns、tool_calls 从历史基线累计；
        cost = 历史成本 + 本次 delta 成本；
        session_id 保持不变。

        Args:
            prompt (str): 本次续跑输入。
            session_snapshot (AgentSessionSnapshot): 已持久化的会话快照。

        Returns:
            AgentRunResult: 续跑后的完整运行结果。
        """
        session_state = AgentSessionState.from_persisted(
            messages=list(session_snapshot.messages),
            transcript=list(session_snapshot.transcript),
            tool_call_count=session_snapshot.tool_calls,
        )
        local_result = self._prepare_prompt(
            prompt=prompt,
            session_state=session_state,
            session_id=session_snapshot.session_id,
            turns_offset=session_snapshot.turns,
            usage_baseline=session_snapshot.usage,
            cost_baseline=session_snapshot.total_cost_usd,
        )
        if local_result is not None:
            return local_result
        return self._execute_loop(
            session_state=session_state,
            session_id=session_snapshot.session_id,
            turns_offset=session_snapshot.turns,
            usage_baseline=session_snapshot.usage,
            cost_baseline=session_snapshot.total_cost_usd,
        )

    def __post_init__(self) -> None:
        """内部方法：执行 `__post_init__` 相关逻辑。
        Args:
            None: 无参数。
        Returns:
            None: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        self.search_runtime = SearchRuntime.from_workspace(self.runtime_config.cwd)
        self.mcp_runtime = MCPRuntime.from_workspace(self.runtime_config.cwd)
        self.tool_registry = self._register_workspace_runtime_tools(self.tool_registry)
        self.plugin_runtime = PluginRuntime.from_workspace(self.runtime_config.cwd, self.tool_registry)
        self.tool_registry = self.plugin_runtime.merge_tool_registry(self.tool_registry)
        self.hook_policy_runtime = HookPolicyRuntime.from_workspace(self.runtime_config.cwd)
        self.tool_registry = self.hook_policy_runtime.filter_tool_registry(self.tool_registry)
        self.runtime_config = self.hook_policy_runtime.apply_runtime_config(self.runtime_config)
        self.context_compactor = ContextCompactor(self.client)
        self.budget_context_orchestrator = BudgetContextOrchestrator(
            budget_evaluator=self.budget_evaluator,
            context_snipper=self.context_snipper,
            context_compactor=self.context_compactor,
        )
        self.slash_dispatcher = SlashCommandDispatcher(self.budget_evaluator)

    def _register_workspace_runtime_tools(
        self,
        tool_registry: dict[str, AgentTool],
    ) -> dict[str, AgentTool]:
        """内部方法：执行 `_register_workspace_runtime_tools` 相关逻辑。
        Args:
            tool_registry (dict[str, AgentTool]): 参数 `tool_registry`。
        Returns:
            dict[str, AgentTool]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        merged_registry = dict(tool_registry)

        if self.search_runtime.providers:
            merged_registry['workspace_search'] = AgentTool(
                name='workspace_search',
                description='Search the configured workspace search provider and return structured web results.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string'},
                        'provider_id': {'type': 'string'},
                        'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                        'max_retries': {'type': 'integer', 'minimum': 0, 'maximum': 3},
                    },
                    'required': ['query'],
                },
                handler=self._run_workspace_search,
            )

        if self.mcp_runtime.resources or self.mcp_runtime.servers:
            merged_registry.update(
                {
                    'mcp_list_resources': AgentTool(
                        name='mcp_list_resources',
                        description='List MCP resources discovered from local manifests and configured MCP servers.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'query': {'type': 'string'},
                                'server_name': {'type': 'string'},
                                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                            },
                        },
                        handler=self._run_mcp_list_resources,
                    ),
                    'mcp_read_resource': AgentTool(
                        name='mcp_read_resource',
                        description='Read a specific MCP resource by URI.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'uri': {'type': 'string'},
                                'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 20000},
                            },
                            'required': ['uri'],
                        },
                        handler=self._run_mcp_read_resource,
                    ),
                    'mcp_list_tools': AgentTool(
                        name='mcp_list_tools',
                        description='List tools exposed by configured MCP servers.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'query': {'type': 'string'},
                                'server_name': {'type': 'string'},
                                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                            },
                        },
                        handler=self._run_mcp_list_tools,
                    ),
                    'mcp_call_tool': AgentTool(
                        name='mcp_call_tool',
                        description='Call a tool exposed by an MCP server with JSON object arguments.',
                        parameters={
                            'type': 'object',
                            'properties': {
                                'tool_name': {'type': 'string'},
                                'server_name': {'type': 'string'},
                                'arguments': {
                                    'type': 'object',
                                    'additionalProperties': True,
                                },
                                'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 20000},
                            },
                            'required': ['tool_name'],
                        },
                        handler=self._run_mcp_call_tool,
                    ),
                }
            )

        return merged_registry

    def _run_workspace_search(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """内部方法：执行 `_run_workspace_search` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            context (Any): 参数 `context`。
        Returns:
            str | tuple[str, JSONDict]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        query = self._require_tool_string(arguments, 'query')
        provider_id = self._optional_tool_string(arguments, 'provider_id')
        max_results = self._optional_tool_int(arguments, 'max_results', min_value=1, max_value=20)
        max_retries = self._optional_tool_int(arguments, 'max_retries', min_value=0, max_value=3) or 0

        try:
            response = self.search_runtime.search(
                query,
                provider_id=provider_id,
                max_results=max_results,
                max_retries=max_retries,
            )
        except (SearchQueryError, ValueError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        lines = [
            '# Search Results',
            '',
            f'- Provider: {response.provider.provider_id}',
            f'- Query: {response.query}',
            f'- Attempts: {response.attempts}',
            '',
        ]
        if not response.results:
            lines.append('No results returned.')
        else:
            for item in response.results:
                lines.extend(
                    [
                        f'{item.rank}. {item.title}',
                        f'URL: {item.url}',
                        f'Snippet: {item.snippet}',
                        '',
                    ]
                )

        content = self._truncate_tool_output('\n'.join(lines).rstrip(), context.max_output_chars)
        return (
            content,
            {
                'provider_id': response.provider.provider_id,
                'attempts': response.attempts,
                'result_count': len(response.results),
            },
        )

    def _run_mcp_list_resources(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """内部方法：执行 `_run_mcp_list_resources` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            context (Any): 参数 `context`。
        Returns:
            str | tuple[str, JSONDict]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        query = self._optional_tool_string(arguments, 'query')
        server_name = self._optional_tool_string(arguments, 'server_name')
        limit = self._optional_tool_int(arguments, 'limit', min_value=1, max_value=100) or 20

        try:
            resources = self.mcp_runtime.list_resources(query=query, server_name=server_name, limit=limit)
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        content = self._truncate_tool_output(
            self.mcp_runtime.render_resource_index(query=query, limit=limit),
            context.max_output_chars,
        )
        return content, {'resource_count': len(resources), 'server_name': server_name or ''}

    def _run_mcp_read_resource(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """内部方法：执行 `_run_mcp_read_resource` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            context (Any): 参数 `context`。
        Returns:
            str | tuple[str, JSONDict]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        uri = self._require_tool_string(arguments, 'uri')
        max_chars = self._resolve_tool_output_limit(arguments, context, key='max_chars')

        try:
            content = self.mcp_runtime.render_resource(uri, max_chars=max_chars)
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        return content, {'uri': uri}

    def _run_mcp_list_tools(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """内部方法：执行 `_run_mcp_list_tools` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            context (Any): 参数 `context`。
        Returns:
            str | tuple[str, JSONDict]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        query = self._optional_tool_string(arguments, 'query')
        server_name = self._optional_tool_string(arguments, 'server_name')
        limit = self._optional_tool_int(arguments, 'limit', min_value=1, max_value=100) or 50

        try:
            tools = self.mcp_runtime.list_tools(query=query, server_name=server_name, limit=limit)
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        content = self._truncate_tool_output(
            self.mcp_runtime.render_tool_index(query=query, server_name=server_name, limit=limit),
            context.max_output_chars,
        )
        return content, {'tool_count': len(tools), 'server_name': server_name or ''}

    def _run_mcp_call_tool(
        self,
        arguments: JSONDict,
        context,
    ) -> str | tuple[str, JSONDict]:
        """内部方法：执行 `_run_mcp_call_tool` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            context (Any): 参数 `context`。
        Returns:
            str | tuple[str, JSONDict]: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        tool_name = self._require_tool_string(arguments, 'tool_name')
        server_name = self._optional_tool_string(arguments, 'server_name')
        raw_tool_arguments = arguments.get('arguments', {})
        if raw_tool_arguments is None:
            raw_tool_arguments = {}
        if not isinstance(raw_tool_arguments, dict):
            raise ToolExecutionError('mcp_call_tool.arguments must be a JSON object')

        max_chars = self._resolve_tool_output_limit(arguments, context, key='max_chars')

        try:
            result = self.mcp_runtime.call_tool(
                tool_name,
                arguments=dict(raw_tool_arguments),
                server_name=server_name,
                max_chars=max_chars,
            )
        except (MCPTransportError, ValueError, FileNotFoundError) as exc:
            raise ToolExecutionError(str(exc)) from exc

        content = self._truncate_tool_output(
            self.mcp_runtime.render_tool_call(
                tool_name,
                arguments=dict(raw_tool_arguments),
                server_name=server_name,
                max_chars=max_chars,
            ),
            context.max_output_chars,
        )
        return content, {'server_name': result.server_name, 'tool_name': result.tool_name, 'is_error': result.is_error}

    @staticmethod
    def _truncate_tool_output(content: str, max_chars: int) -> str:
        """内部方法：执行 `_truncate_tool_output` 相关逻辑。
        Args:
            content (str): 参数 `content`。
            max_chars (int): 参数 `max_chars`。
        Returns:
            str: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        if len(content) <= max_chars:
            return content
        omitted = len(content) - max_chars
        suffix = f'\n\n... truncated {omitted} characters'
        keep = max(0, max_chars - len(suffix))
        return content[:keep] + suffix

    @staticmethod
    def _require_tool_string(arguments: JSONDict, key: str) -> str:
        """内部方法：执行 `_require_tool_string` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            key (str): 参数 `key`。
        Returns:
            str: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ToolExecutionError(f'{key} must be a non-empty string')
        return value.strip()

    @staticmethod
    def _optional_tool_string(arguments: JSONDict, key: str) -> str | None:
        """内部方法：执行 `_optional_tool_string` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            key (str): 参数 `key`。
        Returns:
            str | None: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ToolExecutionError(f'{key} must be a string when provided')
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _optional_tool_int(
        arguments: JSONDict,
        key: str,
        *,
        min_value: int,
        max_value: int,
    ) -> int | None:
        """内部方法：执行 `_optional_tool_int` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            key (str): 参数 `key`。
            min_value (int): 参数 `min_value`。
            max_value (int): 参数 `max_value`。
        Returns:
            int | None: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolExecutionError(f'{key} must be an integer when provided')
        if value < min_value or value > max_value:
            raise ToolExecutionError(f'{key} must be between {min_value} and {max_value}')
        return value

    def _resolve_tool_output_limit(
        self,
        arguments: JSONDict,
        context,
        *,
        key: str,
    ) -> int:
        """内部方法：执行 `_resolve_tool_output_limit` 相关逻辑。
        Args:
            arguments (JSONDict): 参数 `arguments`。
            context (Any): 参数 `context`。
            key (str): 参数 `key`。
        Returns:
            int: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        requested = self._optional_tool_int(arguments, key, min_value=1, max_value=20000)
        if requested is None:
            return context.max_output_chars
        return min(requested, context.max_output_chars)

    def _prepare_prompt(
        self,
        *,
        prompt: str,
        session_state: AgentSessionState,
        session_id: str,
        turns_offset: int,
        usage_baseline: TokenUsage,
        cost_baseline: float,
    ) -> AgentRunResult | None:
        """在 prompt 写入 session_state 前执行 slash 分流。

        Args:
            prompt (str): 用户输入。
            session_state (AgentSessionState): 当前会话状态。
            session_id (str): 当前会话 ID。
            turns_offset (int): 历史已完成轮次。
            usage_baseline (TokenUsage): 历史 token 基线。
            cost_baseline (float): 历史成本基线。

        Returns:
            AgentRunResult | None: slash 本地处理有结果时返回 AgentRunResult，否则返回 None。
        """
        slash_result = self.slash_dispatcher.dispatch_slash_command(
            SlashCommandContext(
                session_state=session_state,
                session_id=session_id,
                turns_offset=turns_offset,
                runtime_config=self.runtime_config,
                model_config=self.client.model_config,
                tool_registry=self.tool_registry,
                plugin_summary=self.plugin_runtime.render_summary(),
            ),
            prompt,
        )

        if not slash_result.handled:
            session_state.append_user(slash_result.prompt or prompt)
            return None

        if slash_result.continue_query:
            session_state.append_user(slash_result.prompt or prompt)
            return None

        return self._build_slash_result(
            slash_result,
            session_state=session_state,
            session_id=session_id,
            turns_offset=turns_offset,
            usage_baseline=usage_baseline,
            cost_baseline=cost_baseline,
        )

    def _build_slash_result(
        self,
        slash_result: SlashCommandResult,
        *,
        session_state: AgentSessionState,
        session_id: str,
        turns_offset: int,
        usage_baseline: TokenUsage,
        cost_baseline: float,
    ) -> AgentRunResult:
        """构造本地 slash 命令结果并落盘。

        Returns:
            AgentRunResult: slash 命令对应的标准运行结果对象。
        """
        effective_session_state = slash_result.replacement_session_state or session_state
        effective_session_id = uuid4().hex if slash_result.fork_session else session_id

        if slash_result.fork_session:
            effective_turns = 0
            effective_usage_total = TokenUsage()
            effective_usage_delta = TokenUsage()
            effective_cost_baseline = 0.0
        else:
            effective_turns = turns_offset
            effective_usage_total = usage_baseline
            effective_usage_delta = TokenUsage()
            effective_cost_baseline = cost_baseline

        event = self._make_slash_event(
            slash_result,
            session_id_before=session_id,
            session_id_after=effective_session_id,
        )
        return self._build_run_result(
            session_id=effective_session_id,
            session_state=effective_session_state,
            final_output=self._format_slash_output(
                slash_result,
                session_id_before=session_id,
                session_id_after=effective_session_id,
            ),
            turns_total=effective_turns,
            usage_delta=effective_usage_delta,
            usage_total=effective_usage_total,
            cost_baseline=effective_cost_baseline,
            stop_reason='slash_command',
            events=[event],
        )

    @staticmethod
    def _make_slash_event(
        slash_result: SlashCommandResult,
        *,
        session_id_before: str,
        session_id_after: str,
    ) -> JSONDict:
        """内部方法：执行 `_make_slash_event` 相关逻辑。
        Args:
            slash_result (SlashCommandResult): 参数 `slash_result`。
            session_id_before (str): 参数 `session_id_before`。
            session_id_after (str): 参数 `session_id_after`。
        Returns:
            JSONDict: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        event: JSONDict = {
            'type': 'slash_command',
            'command': slash_result.command_name,
            'continue_query': slash_result.continue_query,
            'mode': 'mutating' if slash_result.fork_session else 'read_only',
            'session_id_before': session_id_before,
            'session_id_after': session_id_after,
        }
        for key, value in slash_result.metadata.items():
            event[key] = value
        return event

    @staticmethod
    def _format_slash_output(
        slash_result: SlashCommandResult,
        *,
        session_id_before: str,
        session_id_after: str,
    ) -> str:
        """内部方法：执行 `_format_slash_output` 相关逻辑。
        Args:
            slash_result (SlashCommandResult): 参数 `slash_result`。
            session_id_before (str): 参数 `session_id_before`。
            session_id_after (str): 参数 `session_id_after`。
        Returns:
            str: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        if slash_result.command_name != 'clear':
            return slash_result.output

        lines = [slash_result.output]
        if slash_result.metadata.get('had_history'):
            lines.append(f'Previous session id: {session_id_before}')
        lines.append(f'Cleared session id: {session_id_after}')
        return '\n'.join(lines)

    def _execute_loop(
        self,
        *,
        session_state: AgentSessionState,
        session_id: str,
        turns_offset: int,
        usage_baseline: TokenUsage,
        cost_baseline: float,
    ) -> AgentRunResult:
        """run / resume 共用的 turn loop。

        usage_delta  只统计本次执行的增量。
        usage_total  = usage_baseline + usage_delta。
        cost         = cost_baseline + estimate_cost_usd(usage_delta)。

        Returns:
            AgentRunResult: 达到停止条件后的最终运行结果。
        """
        events: list[JSONDict] = []
        usage_delta = TokenUsage()
        final_output = ''
        turns_this_run = 0
        stop_reason = 'max_turns'
        model_call_count = 0

        guard = BudgetGuard(
            budget=self.runtime_config.budget_config,
            pricing=self.client.model_config.pricing,
            cost_baseline=cost_baseline,
        )
        tool_context = build_tool_context(
            self.runtime_config,
            tool_registry=self.tool_registry,
            safe_env=self.hook_policy_runtime.safe_env,
        )

        for turn_index in range(1, self.runtime_config.max_turns + 1):
            turns_this_run = turn_index

            openai_tools = self._build_openai_tools()
            pre_model_outcome = self.budget_context_orchestrator.run_pre_model_cycle(
                session_state=session_state,
                runtime_config=self.runtime_config,
                guard=guard,
                openai_tools=openai_tools,
                turn_index=turn_index,
                turns_offset=turns_offset,
                turns_this_run=turns_this_run,
                usage_delta=usage_delta,
                model_call_count=model_call_count,
            )
            usage_delta = pre_model_outcome.usage_delta
            model_call_count = pre_model_outcome.model_call_count
            pre_model_stop = pre_model_outcome.pre_model_stop
            events.extend(pre_model_outcome.events)

            # 模型调用前四维预算检查（session_turns / model_calls / token / cost）
            if pre_model_stop is not None:
                return self._early_stop(
                    pre_model_stop,
                    session_id=session_id, session_state=session_state, final_output=final_output,
                    turns_total=turns_offset + turns_this_run, usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta, cost_baseline=cost_baseline,
                    turn_index=turn_index, events=events,
                )

            response = self._complete_with_reactive_compact(
                session_state=session_state,
                openai_tools=openai_tools,
                turn_index=turn_index,
                events=events,
                guard=guard,
                turns_offset=turns_offset,
                turns_this_run=turns_this_run,
                usage_delta=usage_delta,
                model_call_count=model_call_count,
            )
            response, usage_delta, model_call_count, reactive_stop = response
            if reactive_stop is not None:
                return self._early_stop(
                    reactive_stop,
                    session_id=session_id, session_state=session_state, final_output=final_output,
                    turns_total=turns_offset + turns_this_run, usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta, cost_baseline=cost_baseline,
                    turn_index=turn_index, events=events,
                )
            if response is None:
                stop_reason = 'backend_error'
                return self._build_run_result(
                    session_id=session_id,
                    session_state=session_state,
                    final_output=final_output,
                    turns_total=turns_offset + turns_this_run,
                    usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta,
                    cost_baseline=cost_baseline,
                    stop_reason=stop_reason,
                    events=events,
                )

            session_state.append_assistant_turn(response)
            if response.content:
                final_output = response.content

            events.append({
                'type': 'model_turn',
                'turn': turn_index,
                'finish_reason': response.finish_reason,
                'tool_calls': len(response.tool_calls),
            })

            # 没有工具调用时，说明当前任务已收敛
            if not response.tool_calls:
                stop_reason = response.finish_reason or 'completed'
                return self._build_run_result(
                    session_id=session_id,
                    session_state=session_state,
                    final_output=final_output,
                    turns_total=turns_offset + turns_this_run,
                    usage_delta=usage_delta,
                    usage_total=usage_baseline + usage_delta,
                    cost_baseline=cost_baseline,
                    stop_reason=stop_reason,
                    events=events,
                )

            # 执行工具调用并回填结果
            for tool_call in response.tool_calls:
                before_hooks = self.plugin_runtime.get_before_hooks(tool_call.name) + self.hook_policy_runtime.get_before_hooks(tool_call.name)
                after_hooks = self.plugin_runtime.get_after_hooks(tool_call.name) + self.hook_policy_runtime.get_after_hooks(tool_call.name)

                for hook in before_hooks:
                    self._append_tool_hook_message(
                        session_state,
                        hook=hook,
                        tool_call=tool_call,
                        turn_index=turn_index,
                        events=events,
                    )

                metadata_updates: JSONDict = {
                    'preflight_sources': [hook['source'] for hook in before_hooks],
                    'after_hook_sources': [hook['source'] for hook in after_hooks],
                }

                block_decision = self.hook_policy_runtime.resolve_block(tool_call.name)
                if block_decision is None:
                    block_decision = self.plugin_runtime.resolve_block(tool_call.name)

                if block_decision is not None:
                    tool_result = self._make_blocked_tool_result(
                        tool_call,
                        block_decision,
                        metadata_updates,
                    )
                    events.append(
                        {
                            'type': 'tool_blocked',
                            'turn': turn_index,
                            'tool_call_id': tool_call.id,
                            'tool_name': tool_call.name,
                            'source': block_decision['source'],
                            'source_name': block_decision['source_name'],
                            'reason': block_decision['reason'],
                        }
                    )
                else:
                    tool_result = execute_tool(
                        self.tool_registry,
                        tool_call.name,
                        tool_call.arguments,
                        tool_context,
                    )
                    tool_result = self._merge_tool_result_metadata(tool_result, metadata_updates)

                session_state.append_tool_result(tool_call, tool_result)

                for hook in after_hooks:
                    self._append_tool_hook_message(
                        session_state,
                        hook=hook,
                        tool_call=tool_call,
                        turn_index=turn_index,
                        events=events,
                    )

                events.append({
                    'type': 'tool_result',
                    'turn': turn_index,
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'ok': tool_result.ok,
                    'error_kind': tool_result.metadata.get('error_kind'),
                    'metadata': dict(tool_result.metadata),
                })

                # 工具执行后预算检查
                if stop := guard.check_post_tool(session_state.tool_call_count):
                    return self._early_stop(
                        stop,
                        session_id=session_id, session_state=session_state, final_output=final_output,
                        turns_total=turns_offset + turns_this_run, usage_delta=usage_delta,
                        usage_total=usage_baseline + usage_delta, cost_baseline=cost_baseline,
                        turn_index=turn_index, events=events,
                    )

        # 达到最大轮数限制，返回结果
        return self._build_run_result(
            session_id=session_id,
            session_state=session_state,
            final_output=final_output,
            turns_total=turns_offset + turns_this_run,
            usage_delta=usage_delta,
            usage_total=usage_baseline + usage_delta,
            cost_baseline=cost_baseline,
            stop_reason=stop_reason,
            events=events,
        )

    def _early_stop(
        self,
        stop_reason: str,
        *,
        session_id: str,
        session_state: AgentSessionState,
        final_output: str,
        turns_total: int,
        usage_delta: TokenUsage,
        usage_total: TokenUsage,
        cost_baseline: float,
        turn_index: int,
        events: list[JSONDict],
    ) -> AgentRunResult:
        """预算闸门触发时的统一提前退出路径。

        统一追加 budget_stop 事件并调用 _build_run_result，
        消除六处重复的事件追加 + 结果构建模式。
        """
        events.append({'type': 'budget_stop', 'reason': stop_reason, 'turn': turn_index})
        return self._build_run_result(
            session_id=session_id,
            session_state=session_state,
            final_output=final_output,
            turns_total=turns_total,
            usage_delta=usage_delta,
            usage_total=usage_total,
            cost_baseline=cost_baseline,
            stop_reason=stop_reason,
            events=events,
        )

    def _complete_with_reactive_compact(
        self,
        *,
        session_state: AgentSessionState,
        openai_tools: list[JSONDict],
        turn_index: int,
        events: list[JSONDict],
        guard: BudgetGuard,
        turns_offset: int,
        turns_this_run: int,
        usage_delta: TokenUsage,
        model_call_count: int,
    ) -> tuple[OneTurnResponse | None, TokenUsage, int, str | None]:
        """执行一次模型调用；必要时在 context-length 错误后进行 reactive compact 重试。"""
        reactive_outcome = self.budget_context_orchestrator.complete_with_reactive_compact(
            client=self.client,
            session_state=session_state,
            runtime_config=self.runtime_config,
            openai_tools=openai_tools,
            turn_index=turn_index,
            guard=guard,
            turns_offset=turns_offset,
            turns_this_run=turns_this_run,
            usage_delta=usage_delta,
            model_call_count=model_call_count,
        )
        events.extend(reactive_outcome.events)
        return (
            reactive_outcome.response,
            reactive_outcome.usage_delta,
            reactive_outcome.model_call_count,
            reactive_outcome.stop_reason,
        )

    def _build_openai_tools(self) -> list[JSONDict]:
        """构建发送给模型的工具定义列表。"""
        return [tool.to_openai_tool() for tool in self.tool_registry.values()]

    @staticmethod
    def _merge_tool_result_metadata(
        result: ToolExecutionResult,
        metadata_updates: JSONDict,
    ) -> ToolExecutionResult:
        """内部方法：执行 `_merge_tool_result_metadata` 相关逻辑。
        Args:
            result (ToolExecutionResult): 参数 `result`。
            metadata_updates (JSONDict): 参数 `metadata_updates`。
        Returns:
            ToolExecutionResult: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        merged_metadata = dict(result.metadata)
        for key, value in metadata_updates.items():
            if value:
                merged_metadata[key] = value
        return ToolExecutionResult(
            name=result.name,
            ok=result.ok,
            content=result.content,
            metadata=merged_metadata,
        )

    def _make_blocked_tool_result(
        self,
        tool_call: ToolCall,
        block_decision: JSONDict,
        metadata_updates: JSONDict,
    ) -> ToolExecutionResult:
        """内部方法：执行 `_make_blocked_tool_result` 相关逻辑。
        Args:
            tool_call (ToolCall): 参数 `tool_call`。
            block_decision (JSONDict): 参数 `block_decision`。
            metadata_updates (JSONDict): 参数 `metadata_updates`。
        Returns:
            ToolExecutionResult: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        merged_metadata = dict(metadata_updates)
        merged_metadata.update(
            {
                'error_kind': 'tool_blocked',
                'blocked_by': block_decision['source'],
                'blocked_by_name': block_decision['source_name'],
                'block_reason': block_decision['reason'],
            }
        )
        return ToolExecutionResult(
            name=tool_call.name,
            ok=False,
            content=str(block_decision['message']),
            metadata=merged_metadata,
        )

    @staticmethod
    def _append_tool_hook_message(
        session_state: AgentSessionState,
        *,
        hook: JSONDict,
        tool_call: ToolCall,
        turn_index: int,
        events: list[JSONDict],
    ) -> None:
        """内部方法：执行 `_append_tool_hook_message` 相关逻辑。
        Args:
            session_state (AgentSessionState): 参数 `session_state`。
            hook (JSONDict): 参数 `hook`。
            tool_call (ToolCall): 参数 `tool_call`。
            turn_index (int): 参数 `turn_index`。
            events (list[JSONDict]): 参数 `events`。
        Returns:
            None: 函数返回结果。
        Raises:
            Exception: 按调用链透传的异常。
        """
        phase = str(hook.get('phase', 'before'))
        event_type = 'tool_preflight' if phase == 'before' else 'tool_after_hook'
        metadata = {
            'phase': phase,
            'tool_call_id': tool_call.id,
            'tool_name': tool_call.name,
            'source': hook.get('source', 'unknown'),
            'source_name': hook.get('source_name', 'unknown'),
        }
        session_state.append_runtime_message(str(hook.get('content', '')), metadata=metadata)
        events.append(
            {
                'type': event_type,
                'turn': turn_index,
                'tool_call_id': tool_call.id,
                'tool_name': tool_call.name,
                'source': hook.get('source', 'unknown'),
                'source_name': hook.get('source_name', 'unknown'),
                'phase': phase,
            }
        )

    def _build_run_result(
        self,
        *,
        session_id: str,
        session_state: AgentSessionState,
        final_output: str,
        turns_total: int,
        usage_delta: TokenUsage,
        usage_total: TokenUsage,
        cost_baseline: float,
        stop_reason: str,
        events: list[JSONDict],
    ) -> AgentRunResult:
        """统一构造最终运行结果并落盘会话快照。

        total_cost_usd = cost_baseline + estimate_cost_usd(usage_delta)，
        避免因历史计费策略变化导致重算偏差。
        """
        transcript = session_state.transcript()
        events_snapshot = tuple(dict(item) for item in events)
        delta_cost = self.client.model_config.pricing.estimate_cost_usd(usage_delta)
        total_cost_usd = cost_baseline + delta_cost
        session_snapshot = AgentSessionSnapshot(
            session_id=session_id,
            model_config=self.client.model_config,
            runtime_config=self.runtime_config,
            messages=tuple(session_state.to_messages()),
            transcript=transcript,
            events=events_snapshot,
            final_output=final_output,
            turns=turns_total,
            tool_calls=session_state.tool_call_count,
            usage=usage_total,
            total_cost_usd=total_cost_usd,
            stop_reason=stop_reason,
        )
        session_path = self.session_store.save(session_snapshot)
        return AgentRunResult(
            final_output=final_output,
            turns=turns_total,
            tool_calls=session_state.tool_call_count,
            transcript=transcript,
            events=events_snapshot,
            usage=usage_total,
            total_cost_usd=total_cost_usd,
            stop_reason=stop_reason,
            file_history=session_snapshot.file_history,
            session_id=session_id,
            session_path=str(session_path),
            scratchpad_directory=session_snapshot.scratchpad_directory,
        )
