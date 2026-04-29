"""负责交互式聊天循环与结果渲染。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core_contracts.run_result import AgentRunResult
from interaction.environment_summary import EnvironmentLoadSummary
from interaction.quit_render import ExitRenderer
from interaction.runtime_event_printer import RuntimeEventPrinter
from interaction.session_summary import SessionInteractionTracker
from interaction.slash_autocomplete import SlashAutocompleteEntry, SlashAutocompletePrompt
from interaction.slash_commands import SlashCommandDispatcher
from interaction.slash_render import SlashCommandRenderer
from interaction.startup_render import StartupRenderer
from session.session_snapshot import AgentSessionSnapshot
from session.session_store import AgentSessionStore


@dataclass
class ChatLoop:
    """封装 agent / agent-chat / agent-resume 共用的交互循环。"""

    session_store_cls: type[AgentSessionStore] = AgentSessionStore
    startup_renderer: StartupRenderer = field(default_factory=StartupRenderer)
    exit_renderer: ExitRenderer = field(default_factory=ExitRenderer)
    slash_renderer: SlashCommandRenderer = field(default_factory=SlashCommandRenderer)
    chat_exit_commands: frozenset[str] = frozenset({'/exit', '/quit'})

    def run(
        self,
        agent,
        *,
        current_session_id: str | None,
        current_session_directory: Path | None,
        pending_session_snapshot: AgentSessionSnapshot | None,
        show_progress: bool,
    ) -> int:
        """执行多轮交互循环并返回退出码。"""
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
        """从当前 agent 提炼启动横幅需要的环境摘要。"""
        return EnvironmentLoadSummary(
            mcp_servers=self._count_runtime_items(agent, 'mcp_runtime', 'servers'),
            plugins=self._count_workspace_items(agent, 'plugin_count'),
            hook_policies=self._count_workspace_items(agent, 'policy_count'),
            search_providers=self._count_workspace_items(agent, 'search_provider_count'),
            load_errors=self._count_runtime_load_errors(agent),
        )

    @staticmethod
    def _count_runtime_items(agent: object, runtime_name: str, collection_name: str) -> int:
        """读取指定 runtime 上某个集合字段的长度。"""
        runtime = getattr(agent, runtime_name, None)
        if runtime is None:
            return 0
        value = getattr(runtime, collection_name, ())
        try:
            return len(value)
        except TypeError:
            return 0

    def _count_runtime_load_errors(self, agent: object) -> int:
        """汇总各运行时对象上的 load_errors 数量。"""
        return self._count_runtime_items(agent, 'mcp_runtime', 'load_errors') + self._count_workspace_items(agent, 'load_error_count')

    @staticmethod
    def _count_workspace_items(agent: object, attribute_name: str) -> int:
        """读取 workspace gateway 上某个计数字段的值。"""
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
        """为当前 agent 动态挂载 progress reporter。"""
        reporter = progress_printer.emit if progress_printer is not None else None
        setattr(agent, 'progress_reporter', reporter)

    @staticmethod
    def _flush_progress_printer(progress_printer: RuntimeEventPrinter | None) -> None:
        """输出 progress printer 中尚未刷新的残留片段。"""
        if progress_printer is not None:
            progress_printer.flush()

    def _finalize_interactive_loop(
        self,
        tracker: SessionInteractionTracker,
        *,
        leading_blank_line: bool = False,
    ) -> int:
        """渲染交互结束提示框并返回退出码。"""
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
        """执行单轮聊天输入，在 run 与 resume 之间自动分流。"""
        if current_session_id:
            effective_snapshot = session_snapshot
            if effective_snapshot is None or effective_snapshot.session_id != current_session_id:
                effective_snapshot = self._load_session_snapshot(
                    current_session_id,
                    directory=current_session_directory,
                )
            return agent.resume(prompt, effective_snapshot)
        return agent.run(prompt)

    @staticmethod
    def _build_slash_autocomplete_prompt(agent) -> SlashAutocompletePrompt:
        """根据 agent 的 slash 命令表构造自动补全输入读取器。"""
        slash_dispatcher = getattr(agent, 'slash_dispatcher', None)
        if slash_dispatcher is None:
            slash_dispatcher = SlashCommandDispatcher()

        slash_entries = tuple(
            SlashAutocompleteEntry(name=name, description=spec.description)
            for spec in slash_dispatcher.get_slash_command_specs()
            for name in spec.names
        )
        return SlashAutocompletePrompt(entries=slash_entries)

    def _load_session_snapshot(
        self,
        session_id: str,
        *,
        directory: Path | None,
    ) -> AgentSessionSnapshot:
        """按会话 ID 从持久化存储中加载快照。"""
        session_store = self.session_store_cls(directory)
        return session_store.load(session_id)

    def _render_chat_result(self, result: AgentRunResult) -> None:
        """把单轮执行结果渲染到标准输出。"""
        slash_event = self._find_slash_event(result)
        if result.final_output and (slash_event is not None or result.stop_reason == 'slash_command'):
            self._render_slash_result(result, slash_event)
        elif result.final_output:
            print(result.final_output)
        elif fallback_message := self._derive_empty_result_message(result):
            print(fallback_message)

    def _render_slash_result(
        self,
        result: AgentRunResult,
        slash_event: dict[str, object] | None,
    ) -> None:
        """把 slash 结果交给专用渲染器输出。"""
        metadata = dict(slash_event or {})
        metadata.pop('type', None)
        command_name = self._extract_slash_command_name(slash_event, result.final_output)
        self.slash_renderer.render(
            command_name=command_name,
            output=result.final_output,
            metadata=metadata,
        )

    @staticmethod
    def _find_slash_event(result: AgentRunResult) -> dict[str, object] | None:
        """从事件列表中找到最近的 slash_command 事件。"""
        for event in reversed(result.events):
            if event.get('type') != 'slash_command':
                continue
            return dict(event)
        return None

    @staticmethod
    def _extract_slash_command_name(
        slash_event: dict[str, object] | None,
        final_output: str,
    ) -> str:
        """优先从事件载荷中提取命令名，必要时根据输出文本回退猜测。"""
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
        """当 final_output 为空时，从 stop_reason 或 events 里提取可读诊断信息。"""
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
        """根据本轮执行结果推进会话 ID 与目录状态。"""
        next_session_id = result.session_id or current_session_id
        next_directory = current_session_directory
        if result.session_path:
            next_directory = Path(result.session_path).resolve().parent
        return next_session_id, next_directory
