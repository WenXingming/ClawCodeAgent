"""负责交互式聊天循环与单轮结果渲染。

本模块是 app 领域的纯内部实现，禁止外部直接导入。
ChatLoop 封装 agent / agent-chat / agent-resume 三条命令共用的多轮交互主循环，
包括启动横幅渲染、slash 自动补全、progress 输出、结果分流渲染和会话状态推进。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core_contracts.run_result import AgentRunResult
from interaction.interaction_gateway import (
    EnvironmentLoadSummary,
    ExitRenderer,
    RuntimeEventPrinter,
    SessionInteractionTracker,
    SlashAutocompleteEntry,
    SlashAutocompletePrompt,
    SlashCommandDispatcher,
    SlashCommandRenderer,
    StartupRenderer,
)
from session.session_gateway import AgentSessionSnapshot, SessionGateway


@dataclass
class ChatLoop:
    """封装 agent / agent-chat / agent-resume 三条命令共用的多轮交互循环。

    核心工作流：
        1. 渲染启动横幅（EnvironmentLoadSummary）；
        2. 挂载 progress reporter，构建 slash 自动补全输入读取器；
        3. 进入 while-True 循环，每轮读取用户输入 -> 执行 -> 渲染结果 -> 推进状态；
        4. 捕获 EOF / KeyboardInterrupt 或 exit 命令时渲染交互摘要后退出。
    """

    session_manager_cls: type[SessionGateway] = SessionGateway  # 可注入的会话管理器类，用于按 ID 加载快照。
    startup_renderer: StartupRenderer = field(default_factory=StartupRenderer)  # 负责渲染启动横幅和环境摘要。
    exit_renderer: ExitRenderer = field(default_factory=ExitRenderer)  # 负责渲染退出时的交互统计摘要。
    slash_renderer: SlashCommandRenderer = field(default_factory=SlashCommandRenderer)  # 负责渲染 slash 命令的结构化输出。
    chat_exit_commands: frozenset[str] = frozenset({'/exit', '/quit'})  # 触发退出的 slash 命令集合。

    def run(
        self,
        agent,
        *,
        current_session_id: str | None,
        current_session_directory: Path | None,
        pending_session_snapshot: AgentSessionSnapshot | None,
        show_progress: bool,
    ) -> int:
        """执行多轮交互循环并返回退出码。

        渲染启动横幅后进入 while-True 循环，每轮读取用户输入，
        分流到 slash / exit / 正常对话三条路径，执行完毕后渲染结果并推进状态。

        Args:
            agent: 已构建好的 Agent 实例，须实现 run / resume / slash_dispatcher 接口。
            current_session_id (str | None): 当前关联的会话 ID；None 表示新会话。
            current_session_directory (Path | None): 会话快照所在目录；None 时使用 agent 默认。
            pending_session_snapshot (AgentSessionSnapshot | None): 恢复模式下预先加载的快照。
            show_progress (bool): 是否在执行期间向 stdout 输出 progress 事件。
        Returns:
            int: 退出码，0 表示正常退出。
        Raises:
            无（内部所有已知异常已被捕获）。
        """
        environment_summary = self._build_environment_load_summary(agent)
        self.startup_renderer.render(environment_summary=environment_summary)
        progress_printer = RuntimeEventPrinter() if show_progress else None
        self._configure_agent_progress(agent, progress_printer)
        prompt_reader = self._build_slash_autocomplete_prompt(agent)
        interaction_tracker = SessionInteractionTracker.start(current_session_id)
        while True:
            try:
                prompt = prompt_reader.read('agent> ')
            except EOFError:
                self._flush_progress_printer(progress_printer)
                return self._finalize_interactive_loop(interaction_tracker, leading_blank_line=True)
            except KeyboardInterrupt:
                self._flush_progress_printer(progress_printer)
                return self._finalize_interactive_loop(interaction_tracker, leading_blank_line=True)

            normalized = prompt.strip()
            if not normalized:
                continue
            if normalized in self.chat_exit_commands:
                self._flush_progress_printer(progress_printer)
                return self._finalize_interactive_loop(interaction_tracker)

            result = self._execute_chat_turn(
                agent,
                prompt=prompt,
                current_session_id=current_session_id,
                current_session_directory=current_session_directory,
                session_snapshot=pending_session_snapshot,
            )
            pending_session_snapshot = None
            self._flush_progress_printer(progress_printer)
            self._render_chat_result(result)
            current_session_id, current_session_directory = self._advance_chat_state(
                result,
                current_session_id=current_session_id,
                current_session_directory=current_session_directory,
            )
            interaction_tracker.observe_run_result(
                result,
                current_session_id=current_session_id,
            )
            print()

    def _build_environment_load_summary(self, agent: object) -> EnvironmentLoadSummary:
        """从当前 agent 提炼启动横幅需要的环境摘要。

        Args:
            agent (object): 已初始化的 Agent 实例。
        Returns:
            EnvironmentLoadSummary: 包含 MCP 服务器数、插件数等环境统计的摘要对象。
        Raises:
            无。
        """
        return EnvironmentLoadSummary(
            mcp_servers=self._count_runtime_items(agent, 'mcp_runtime', 'servers'),
            plugins=self._count_workspace_items(agent, 'plugin_count'),
            hook_policies=self._count_workspace_items(agent, 'policy_count'),
            search_providers=self._count_workspace_items(agent, 'search_provider_count'),
            load_errors=self._count_runtime_load_errors(agent),
        )

    @staticmethod
    def _count_runtime_items(agent: object, runtime_name: str, collection_name: str) -> int:
        """读取指定 runtime 上某个集合字段的长度。

        Args:
            agent (object): 已初始化的 Agent 实例。
            runtime_name (str): agent 上的 runtime 属性名（如 'mcp_runtime'）。
            collection_name (str): runtime 上的集合属性名（如 'servers'）。
        Returns:
            int: 集合元素数量；属性不存在或不支持 len() 时返回 0。
        Raises:
            无。
        """
        runtime = getattr(agent, runtime_name, None)
        if runtime is None:
            return 0
        value = getattr(runtime, collection_name, ())
        try:
            return len(value)
        except TypeError:
            return 0

    def _count_runtime_load_errors(self, agent: object) -> int:
        """汇总各运行时对象上的 load_errors 数量。

        Args:
            agent (object): 已初始化的 Agent 实例。
        Returns:
            int: MCP runtime 与 workspace gateway 上 load_errors 的合计数量。
        Raises:
            无。
        """
        return self._count_runtime_items(agent, 'mcp_runtime', 'load_errors') + self._count_workspace_items(agent, 'load_error_count')

    @staticmethod
    def _count_workspace_items(agent: object, attribute_name: str) -> int:
        """读取 workspace gateway 上某个计数字段的值。

        Args:
            agent (object): 已初始化的 Agent 实例。
            attribute_name (str): workspace_gateway 上的计数属性名（如 'plugin_count'）。
        Returns:
            int: 属性值；属性不存在或不可转换为 int 时返回 0。
        Raises:
            无。
        """
        gateway = getattr(agent, 'workspace_gateway', None)
        if gateway is None:
            return 0
        value = getattr(gateway, attribute_name, 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _configure_agent_progress(
        agent: object,
        progress_printer: RuntimeEventPrinter | None,
    ) -> None:
        """为当前 agent 动态挂载 progress reporter。

        Args:
            agent (object): 已初始化的 Agent 实例。
            progress_printer (RuntimeEventPrinter | None): progress 输出器；None 时清空 reporter。
        Returns:
            None
        Raises:
            无。
        """
        reporter = progress_printer.emit if progress_printer is not None else None
        setattr(agent, 'progress_reporter', reporter)

    @staticmethod
    def _build_slash_autocomplete_prompt(agent) -> SlashAutocompletePrompt:
        """根据 agent 的 slash 命令表构造自动补全输入读取器。

        Args:
            agent: 已初始化的 Agent 实例，slash_dispatcher 属性可选。
        Returns:
            SlashAutocompletePrompt: 已载入全部 slash 命令条目的自动补全输入读取器。
        Raises:
            无。
        """
        slash_dispatcher = getattr(agent, 'slash_dispatcher', None)
        if slash_dispatcher is None:
            slash_dispatcher = SlashCommandDispatcher()

        slash_entries = tuple(
            SlashAutocompleteEntry(name=name, description=spec.description)
            for spec in slash_dispatcher.get_slash_command_specs()
            for name in spec.names
        )
        return SlashAutocompletePrompt(entries=slash_entries)

    @staticmethod
    def _flush_progress_printer(progress_printer: RuntimeEventPrinter | None) -> None:
        """输出 progress printer 中尚未刷新的残留片段。

        Args:
            progress_printer (RuntimeEventPrinter | None): progress 输出器；None 时为空操作。
        Returns:
            None
        Raises:
            无。
        """
        if progress_printer is not None:
            progress_printer.flush()

    def _finalize_interactive_loop(
        self,
        tracker: SessionInteractionTracker,
        *,
        leading_blank_line: bool = False,
    ) -> int:
        """渲染交互结束提示框并返回退出码。

        Args:
            tracker (SessionInteractionTracker): 本次交互的统计追踪器。
            leading_blank_line (bool): 渲染前是否先输出一个空行（EOF / Ctrl-C 场景使用）。
        Returns:
            int: 固定为 0，表示正常退出。
        Raises:
            无。
        """
        if leading_blank_line:
            print()
        self.exit_renderer.render(tracker.to_summary())
        return 0

    def _execute_chat_turn(
        self,
        agent,
        *,
        prompt: str,
        current_session_id: str | None,
        current_session_directory: Path | None,
        session_snapshot: AgentSessionSnapshot | None,
    ) -> AgentRunResult:
        """执行单轮聊天输入，在 run 与 resume 之间自动分流。

        若 current_session_id 存在则确保快照已加载并走 resume 分支；
        否则走 run 分支，开启全新会话。

        Args:
            agent: 已初始化的 Agent 实例。
            prompt (str): 用户本轮输入文本。
            current_session_id (str | None): 当前会话 ID；None 时走 run 分支。
            current_session_directory (Path | None): 快照目录，用于加载会话。
            session_snapshot (AgentSessionSnapshot | None): 已预加载的快照，优先使用。
        Returns:
            AgentRunResult: 本轮执行产出的完整结果。
        Raises:
            无（底层异常向上透传）。
        """
        if current_session_id:
            effective_snapshot = session_snapshot
            if effective_snapshot is None or effective_snapshot.session_id != current_session_id:
                effective_snapshot = self._load_session_snapshot(
                    current_session_id,
                    directory=current_session_directory,
                )
            return agent.resume(prompt, effective_snapshot)
        return agent.run(prompt)

    def _load_session_snapshot(
        self,
        session_id: str,
        *,
        directory: Path | None,
    ) -> AgentSessionSnapshot:
        """按会话 ID 从持久化存储中加载快照。

        Args:
            session_id (str): 待加载的会话唯一标识。
            directory (Path | None): 快照目录；None 时使用 SessionGateway 默认路径。
        Returns:
            AgentSessionSnapshot: 已反序列化的会话快照对象。
        Raises:
            ValueError: 当会话不存在或快照损坏时抛出。
        """
        session_manager = self.session_manager_cls(directory)
        return session_manager.load_session(session_id)

    def _render_chat_result(self, result: AgentRunResult) -> None:
        """把单轮执行结果渲染到标准输出。

        slash 命令结果走专用 slash_renderer 渲染；
        普通结果直接 print；空输出则输出诊断信息（若有）。

        Args:
            result (AgentRunResult): 本轮执行结果。
        Returns:
            None
        Raises:
            无。
        """
        slash_event = self._find_slash_event(result)
        if result.final_output and (slash_event is not None or result.stop_reason == 'slash_command'):
            self._render_slash_result(result, slash_event)
        elif result.final_output:
            print(result.final_output)
        elif fallback_message := self._derive_empty_result_message(result):
            print(fallback_message)

    @staticmethod
    def _find_slash_event(result: AgentRunResult) -> dict[str, object] | None:
        """从事件列表中找到最近的 slash_command 事件。

        Args:
            result (AgentRunResult): 本轮执行结果。
        Returns:
            dict[str, object] | None: 最近的 slash_command 事件字典；不存在时返回 None。
        Raises:
            无。
        """
        for event in reversed(result.events):
            if event.get('type') != 'slash_command':
                continue
            return dict(event)
        return None

    def _render_slash_result(
        self,
        result: AgentRunResult,
        slash_event: dict[str, object] | None,
    ) -> None:
        """把 slash 结果交给专用渲染器输出。

        Args:
            result (AgentRunResult): 本轮执行结果。
            slash_event (dict[str, object] | None): 最近的 slash_command 事件（可为 None）。
        Returns:
            None
        Raises:
            无。
        """
        metadata = dict(slash_event or {})
        metadata.pop('type', None)
        command_name = self._extract_slash_command_name(slash_event, result.final_output)
        self.slash_renderer.render(
            command_name=command_name,
            output=result.final_output,
            metadata=metadata,
        )

    @staticmethod
    def _extract_slash_command_name(
        slash_event: dict[str, object] | None,
        final_output: str,
    ) -> str:
        """优先从事件载荷中提取命令名，必要时根据输出文本回退猜测。

        Args:
            slash_event (dict[str, object] | None): slash_command 事件载荷（可为 None）。
            final_output (str): 本轮 agent 最终输出文本，用于回退匹配。
        Returns:
            str: 提取到的命令名（小写）；无法识别时返回空字符串。
        Raises:
            无。
        """
        if slash_event is not None:
            command_value = slash_event.get('command')
            if isinstance(command_value, str) and command_value.strip():
                return command_value.strip().lower()

        first_line = final_output.splitlines()[0].strip() if final_output.splitlines() else ''
        first_line_map = {
            'Slash Commands': 'help',
            'Context Status': 'context',
            'Session Status': 'status',
            'Permissions': 'permissions',
            'Registered Tools': 'tools',
        }
        if first_line in first_line_map:
            return first_line_map[first_line]
        if final_output.startswith('Cleared in-memory session context.'):
            return 'clear'
        if final_output.startswith('Exiting local session interaction.'):
            return 'exit'
        return ''

    @staticmethod
    def _derive_empty_result_message(result: AgentRunResult) -> str | None:
        """当 final_output 为空时，从 stop_reason 或 events 里提取可读诊断信息。

        Args:
            result (AgentRunResult): 本轮执行结果（final_output 为空时调用）。
        Returns:
            str | None: 可读的诊断提示字符串；无需额外提示时返回 None。
        Raises:
            无。
        """
        for event in reversed(result.events):
            if event.get('type') != 'backend_error':
                continue
            error_text = event.get('error')
            if isinstance(error_text, str) and error_text.strip():
                return f'[error] {error_text.strip()}'

        for event in reversed(result.events):
            if event.get('type') != 'budget_stop':
                continue
            reason = event.get('reason')
            if isinstance(reason, str) and reason.strip():
                return f'[warning] Agent stopped: {reason.strip()}'

        if result.stop_reason and result.stop_reason not in {'stop', 'completed', 'slash_command'}:
            return f'[warning] Agent stopped: {result.stop_reason}'
        return None

    def _advance_chat_state(
        self,
        result: AgentRunResult,
        *,
        current_session_id: str | None,
        current_session_directory: Path | None,
    ) -> tuple[str | None, Path | None]:
        """根据本轮执行结果推进会话 ID 与目录状态。

        Args:
            result (AgentRunResult): 本轮执行结果。
            current_session_id (str | None): 当前会话 ID。
            current_session_directory (Path | None): 当前快照目录。
        Returns:
            tuple[str | None, Path | None]: 推进后的 (next_session_id, next_directory)。
        Raises:
            无。
        """
        next_session_id = result.session_id or current_session_id
        next_directory = current_session_directory
        if result.session_path:
            next_directory = Path(result.session_path).resolve().parent
        return next_session_id, next_directory