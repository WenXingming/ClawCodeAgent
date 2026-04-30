"""interaction 域唯一对外门面（Facade）。

本模块是 interaction 文件夹的唯一公开边界。外部代码必须仅通过 InteractionGateway
访问所有交互能力，严禁直接导入文件夹内的任何其他实现类（如 SlashCommandDispatcher、
TerminalRenderer 等）。

InteractionGateway 封装的能力：
  - CLI 启动横幅与退出摘要渲染
  - slash 命令解析、查找与分发（实现 SlashDispatcher 协议）
  - 运行期结构化事件打印（TTY 状态栏 + 流式日志）
  - 带 slash 自动补全的交互式用户输入读取
  - 跨多轮交互的会话统计累计与 SessionSummary 生成
"""

from __future__ import annotations

import sys
from typing import Callable, Mapping, TextIO

from context.context_gateway import ContextGateway
from core_contracts.interaction_contracts import (
    EnvironmentLoadSummary,
    ParsedSlashCommand,
    SessionSummary,
    SlashAutocompleteEntry,
    SlashCommandContext,
    SlashCommandResolution,
    SlashCommandResult,
    SlashCommandSpec,
)
from core_contracts.outcomes import AgentRunResult
from core_contracts.primitives import JSONDict

from .quit_render import ExitRenderer
from .runtime_event_printer import RuntimeEventPrinter
from .session_summary import SessionInteractionTracker
from .slash_autocomplete import SlashAutocompletePrompt
from .slash_commands import SlashCommandDispatcher
from .slash_render import SlashCommandRenderer
from .startup_render import StartupRenderer


class InteractionGateway:
    """interaction 域唯一对外门面（Facade）。

    本类是交互式 CLI 全生命周期能力的单一入口，将以下七个内部组件的协作
    完全收敛于一个极简公共 API 之后：
      - StartupRenderer   — CLI 启动横幅与环境摘要渲染
      - ExitRenderer      — 会话退出总结渲染
      - SlashCommandRenderer — slash 命令结果面板渲染
      - SlashCommandDispatcher — slash 命令解析、索引与分发
      - RuntimeEventPrinter  — 运行期事件打印（含 TTY spinner 状态栏）
      - SlashAutocompletePrompt — 带 slash 自动补全的交互式输入读取器
      - SessionInteractionTracker — 跨多轮的会话统计累计器

    本类实现 core_contracts.interaction_contracts.SlashDispatcher 协议，可直接用作
    agent 的 slash_dispatcher，也可独立用于 ChatLoop 的渲染与输入职责。

    所有子组件通过构造函数依赖注入；装配逻辑由 interaction/__init__.py 中的
    create_interaction_gateway 工厂函数统一负责。
    """

    def __init__(
        self,
        *,
        context_gateway: ContextGateway | None = None,
        dispatcher: SlashCommandDispatcher,
        startup_renderer: StartupRenderer,
        exit_renderer: ExitRenderer,
        slash_renderer: SlashCommandRenderer,
        event_printer: RuntimeEventPrinter,
        autocomplete_prompt: SlashAutocompletePrompt,
        stream: TextIO | None = None,
        stdin: TextIO | None = None,
    ) -> None:
        """通过依赖注入初始化 interaction 门面。

        所有子组件由外部工厂函数构造后注入；网关本身不负责 new 任何内部类。

        Args:
            context_gateway (ContextGateway | None): Context 领域网关；为 None 时
                /context 命令降级返回提示信息。
            dispatcher (SlashCommandDispatcher): 已装配命令规格的 slash 分发器。
            startup_renderer (StartupRenderer): CLI 启动横幅渲染器。
            exit_renderer (ExitRenderer): CLI 退出总结渲染器。
            slash_renderer (SlashCommandRenderer): slash 命令结果面板渲染器。
            event_printer (RuntimeEventPrinter): 运行期事件打印器，已绑定输出流。
            autocomplete_prompt (SlashAutocompletePrompt): 带 slash 自动补全的输入读取器。
            stream (TextIO | None): 统一 CLI 输出流；None 时默认 sys.stdout。
            stdin (TextIO | None): 统一 CLI 输入流；None 时默认 sys.stdin。
        Returns:
            None: 构造函数仅建立门面内部状态，不执行任何 I/O 操作。
        """
        self._context_gateway = context_gateway
        # ContextGateway | None: /context 命令使用的预算评估入口；可选。

        self._dispatcher = dispatcher
        # SlashCommandDispatcher: slash 命令解析、索引与分发核心（由外部工厂注入）。

        self._startup_renderer = startup_renderer
        # StartupRenderer: CLI 启动横幅渲染器（由外部工厂注入）。

        self._exit_renderer = exit_renderer
        # ExitRenderer: CLI 退出总结渲染器（由外部工厂注入）。

        self._slash_renderer = slash_renderer
        # SlashCommandRenderer: slash 命令结果面板渲染器（由外部工厂注入）。

        self._event_printer = event_printer
        # RuntimeEventPrinter: 运行期结构化事件打印器（由外部工厂注入）。

        self._autocomplete_prompt = autocomplete_prompt
        # SlashAutocompletePrompt: 带 slash 自动补全的交互式输入读取器（由外部工厂注入）。

        self._stream = stream or sys.stdout
        # TextIO: 统一的 CLI 输出流，供公开 API 方法作为 fallback 使用。

        self._stdin = stdin or sys.stdin
        # TextIO: 统一的 CLI 输入流，供公开 API 方法作为 fallback 使用。

        self._session_tracker: SessionInteractionTracker | None = None
        # SessionInteractionTracker | None: 当前会话统计追踪器；由 start_session_tracker() 初始化。

    # ─────────────────────────────────────────────────────────
    # Public API — 渲染（Rendering）
    # ─────────────────────────────────────────────────────────

    def render_startup(
        self,
        stream: TextIO | None = None,
        *,
        environment_summary: EnvironmentLoadSummary | None = None,
    ) -> None:
        """渲染 CLI 启动横幅，并在可用时追加环境加载摘要行。

        Args:
            stream (TextIO | None): 目标输出流；None 时使用构造时注入的默认流。
            environment_summary (EnvironmentLoadSummary | None): 环境加载结果摘要；
                None 时跳过环境摘要行输出。
        Returns:
            None: 该方法只负责将横幅内容写入目标流。
        """
        self._startup_renderer.render(
            stream or self._stream,
            environment_summary=environment_summary,
        )

    def render_exit(
        self,
        summary: SessionSummary,
        stream: TextIO | None = None,
    ) -> None:
        """渲染 CLI 会话退出总结提示框。

        Args:
            summary (SessionSummary): 待渲染的会话汇总快照（工具调用数、耗时等）。
            stream (TextIO | None): 目标输出流；None 时使用构造时注入的默认流。
        Returns:
            None: 该方法只负责将退出提示框写入目标流。
        """
        self._exit_renderer.render(summary, stream or self._stream)

    def render_slash_result(
        self,
        *,
        command_name: str,
        output: str,
        metadata: Mapping[str, object] | None = None,
        stream: TextIO | None = None,
    ) -> None:
        """渲染单次 slash 命令的结构化结果面板。

        Args:
            command_name (str): 已执行的 slash 命令名称（小写，不含前导斜杠）。
            output (str): 命令执行产出的文本内容。
            metadata (Mapping[str, object] | None): 附加元数据；None 时视为空字典。
            stream (TextIO | None): 目标输出流；None 时使用构造时注入的默认流。
        Returns:
            None: 该方法只负责将 slash 面板内容写入目标流。
        """
        self._slash_renderer.render(
            command_name=command_name,
            output=output,
            metadata=metadata,
            stream=stream or self._stream,
        )

    # ─────────────────────────────────────────────────────────
    # Public API — slash 命令（Slash Commands）
    # ─────────────────────────────────────────────────────────

    def dispatch_slash_command(
        self,
        context: SlashCommandContext,
        input_text: str,
    ) -> SlashCommandResult:
        """解析并分发一条用户输入；非 slash 输入透传，不消耗。

        满足 core_contracts.interaction_contracts.SlashDispatcher 协议，可直接用作
        agent 的 slash_dispatcher 属性值。

        Args:
            context (SlashCommandContext): slash 命令执行所需的只读运行时上下文。
            input_text (str): 用户提交的原始输入文本（含前导斜杠或普通 prompt）。
        Returns:
            SlashCommandResult: 分流结果；当输入非 slash 时 handled=False，
                continue_query=True，prompt 字段保留原始输入。
        """
        return self._dispatcher.dispatch_slash_command(context, input_text)

    def parse_slash_command(self, input_text: str) -> ParsedSlashCommand | None:
        """从原始输入中提取 slash 命令结构；普通 prompt 返回 None。

        Args:
            input_text (str): 用户提交的原始输入文本。
        Returns:
            ParsedSlashCommand | None: 成功解析时返回含命令名与参数的结构；否则返回 None。
        """
        return self._dispatcher.parse_slash_command(input_text)

    def resolve_slash_command(self, command_name: str) -> SlashCommandResolution:
        """按精确名或唯一前缀解析 slash 命令规格，支持歧义检测。

        Args:
            command_name (str): 待解析的命令名称；不区分大小写，允许前缀匹配。
        Returns:
            SlashCommandResolution: 解析结果，kind 取值为：
                'exact' / 'prefix' / 'ambiguous' / 'none' / 'empty'。
        """
        return self._dispatcher.resolve_slash_command(command_name)

    def find_slash_command(self, command_name: str) -> SlashCommandSpec | None:
        """按精确名查找 slash 命令规格（不含前缀匹配）。

        Args:
            command_name (str): 待查找的命令名称；不区分大小写，允许首尾空白。
        Returns:
            SlashCommandSpec | None: 精确匹配时返回规格对象，否则返回 None。
        """
        return self._dispatcher.find_slash_command(command_name)

    def get_slash_command_specs(self) -> tuple[SlashCommandSpec, ...]:
        """返回当前支持的全部 slash 命令规格列表（按帮助展示顺序）。

        Returns:
            tuple[SlashCommandSpec, ...]: 已排序的命令规格元组。
        """
        return self._dispatcher.get_slash_command_specs()

    def get_autocomplete_entries(self) -> tuple[SlashAutocompleteEntry, ...]:
        """将当前命令规格投影为自动补全目录条目。

        Returns:
            tuple[SlashAutocompleteEntry, ...]: 可直接传递给
                SlashAutocompletePrompt 的补全条目元组（含命令别名展开）。
        """
        return tuple(
            SlashAutocompleteEntry(name=name, description=spec.description)
            for spec in self._dispatcher.get_slash_command_specs()
            for name in spec.names
        )

    # ─────────────────────────────────────────────────────────
    # Public API — 事件打印（Event Printing）
    # ─────────────────────────────────────────────────────────

    def build_progress_reporter(self) -> Callable[[JSONDict], None]:
        """返回可注入到 agent.progress_reporter 的运行期事件上报回调。

        调用方将返回值赋值给 agent.progress_reporter，agent 在执行期间
        每产生一个结构化事件就调用该回调。

        Returns:
            Callable[[JSONDict], None]: 直接指向内部 RuntimeEventPrinter.emit 的可调用对象。
        """
        return self._event_printer.emit

    def flush_runtime_events(self) -> None:
        """冲刷 RuntimeEventPrinter 中尚未完整输出的工具流残留片段并清空 TTY 状态栏。

        应在每轮 agent 执行结束后、渲染结果前调用，确保 tool_stream 碎片
        不被遗漏，且 TTY 状态栏不残留在输出前。

        Returns:
            None: 该方法只负责刷新缓存与清理显示状态。
        """
        self._event_printer.flush()

    # ─────────────────────────────────────────────────────────
    # Public API — 用户输入（User Input）
    # ─────────────────────────────────────────────────────────

    def read_input(self, prompt_text: str) -> str:
        """读取一轮用户输入，在支持 TTY 时提供 slash 自动补全下拉菜单。

        在非 TTY 或 prompt_toolkit 不可用时自动降级为内建 input()。

        Args:
            prompt_text (str): 显示在输入光标前的提示文本（如 'agent> '）。
        Returns:
            str: 用户输入的原始文本（含首尾空白，由调用方决定是否 strip）。
        Raises:
            EOFError: 用户通过 Ctrl-D 发出 EOF 信号时透传给调用方。
            KeyboardInterrupt: 用户通过 Ctrl-C 中断时透传给调用方。
        """
        return self._autocomplete_prompt.read(prompt_text)

    # ─────────────────────────────────────────────────────────
    # Public API — 会话追踪（Session Tracking）
    # ─────────────────────────────────────────────────────────

    def start_session_tracker(self, session_id: str | None = None) -> None:
        """初始化本次交互的会话统计追踪器并记录起始时间。

        应在 while-True 交互循环开始前调用一次。若在同一实例生命周期内
        多次调用，将重置计时器与所有累计计数。

        Args:
            session_id (str | None): 初始会话 ID；尚未关联会话时可为 None，
                后续可通过 observe_run_result() 自动更新。
        Returns:
            None: 该方法只初始化内部追踪器状态。
        """
        self._session_tracker = SessionInteractionTracker.start(session_id)

    def observe_run_result(
        self,
        result: AgentRunResult,
        *,
        current_session_id: str | None,
    ) -> None:
        """将单轮执行结果的增量统计信息吸收到内部会话追踪器。

        应在每轮 agent.run() / agent.resume() 调用完成后立即调用。
        若 start_session_tracker() 尚未被调用，该方法为安全空操作。

        Args:
            result (AgentRunResult): 本轮执行结果，包含 session_id 与结构化事件列表。
            current_session_id (str | None): 当前已知的活动会话 ID；当 result 未
                显式携带 session_id 时用于回退更新追踪器。
        Returns:
            None: 该方法只更新内部追踪器状态，不产生任何 I/O。
        """
        if self._session_tracker is None:
            return
        self._session_tracker.observe_run_result(result, current_session_id=current_session_id)

    def get_session_summary(self) -> SessionSummary:
        """将当前追踪状态投影为只读会话摘要快照。

        Returns:
            SessionSummary: 包含会话 ID、工具调用统计与挂钟耗时的不可变摘要对象。
                若 start_session_tracker() 尚未调用，则返回全零默认摘要。
        """
        if self._session_tracker is None:
            return SessionSummary()
        return self._session_tracker.to_summary()

