"""控制面命令行交互模块。

本模块负责：
- 暴露 agent、agent-chat、agent-resume 三条命令入口
- 把命令行参数装配为模型配置、运行时配置与会话存储
- 在新会话与恢复会话之间切换，并驱动统一的交互式聊天循环

设计上，本模块以 CLI 作为命令协调器，对外保留模块级 main 兼容入口，
避免破坏上层入口（main.py）及测试的既有导入关系。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from interaction.exit_banner import SessionExitSummaryRenderer, SessionInteractionTracker
from interaction.runtime_event_printer import RuntimeEventPrinter
from interaction.startup_banner import StartupBannerRenderer
from core_contracts.config import AgentPermissions, AgentRuntimeConfig, BudgetConfig, ModelConfig
from core_contracts.model_pricing import ModelPricing
from core_contracts.run_result import AgentRunResult
from openai_client.openai_client import OpenAIClient, OpenAIClientError
from orchestration.local_agent import LocalAgent
from session.session_snapshot import AgentSessionSnapshot
from session.session_store import AgentSessionStore


class CLI:
    """控制面命令行交互协调器。

    该类封装了命令解析、依赖注入、会话恢复、结果渲染与多轮交互循环。
    外部通常通过模块级 main() 创建该类的实例并调用 main()。测试场景下可在
    构造函数中替换 openai_client_cls、agent_cls、session_store_cls，
    从而复用同一套命令流程进行隔离验证。

    典型调用流程::

        cli = CLI(openai_client_cls=..., agent_cls=..., session_store_cls=...)
        exit_code = cli.main(['agent', '--model', 'gpt-4o'])
    """

    _DEFAULT_CHAT_EXIT_COMMANDS: frozenset[str] = frozenset({'/exit', '/quit'})
    # frozenset[str]: 交互式聊天循环中立即结束本地会话的内建命令集合。

    # ------------------------------------------------------------------
    # 构造与初始化
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        openai_client_cls: type[OpenAIClient] = OpenAIClient,
        agent_cls: type[LocalAgent] = LocalAgent,
        session_store_cls: type[AgentSessionStore] = AgentSessionStore,
        banner_renderer: StartupBannerRenderer | None = None,
        exit_summary_renderer: SessionExitSummaryRenderer | None = None,
        chat_exit_commands: frozenset[str] | None = None,
    ) -> None:
        """初始化 CLI 协调器，注入全部可替换的外部依赖。

        Args:
            openai_client_cls (type[OpenAIClient]): 模型客户端构造类型，
                用于按 ModelConfig 创建请求客户端实例。
            agent_cls (type[LocalAgent]): 代理构造类型，
                用于创建新会话或恢复态代理。
            session_store_cls (type[AgentSessionStore]): 会话存储构造类型，
                用于加载与定位持久化会话快照。
            banner_renderer (StartupBannerRenderer | None): 启动横幅渲染器；
                为 None 时使用默认 StartupBannerRenderer 实例。
            exit_summary_renderer (SessionExitSummaryRenderer | None): 会话结束提示框渲染器；
                为 None 时使用默认 SessionExitSummaryRenderer 实例。
            chat_exit_commands (frozenset[str] | None): 交互循环的本地退出命令集合；
                为 None 时使用默认的 {'/exit', '/quit'}。
        Returns:
            None: 初始化完成，不返回值。
        """
        self._openai_client_cls = openai_client_cls
        # type[OpenAIClient]: 模型客户端构造类型，负责根据 ModelConfig 创建 HTTP 请求客户端。

        self._agent_cls = agent_cls
        # type[LocalAgent]: 代理构造类型，承载 run 与 resume 两条执行路径。

        self._session_store_cls = session_store_cls
        # type[AgentSessionStore]: 会话存储构造类型，负责读取持久化的会话快照文件。

        self._banner_renderer = banner_renderer or StartupBannerRenderer()
        # StartupBannerRenderer: 交互循环启动时输出欢迎横幅的渲染器实例。

        self._exit_summary_renderer = exit_summary_renderer or SessionExitSummaryRenderer()
        # SessionExitSummaryRenderer: 交互循环结束时输出总结提示框的渲染器实例。

        self._chat_exit_commands = chat_exit_commands or self._DEFAULT_CHAT_EXIT_COMMANDS
        # frozenset[str]: 交互式聊天循环的本地退出命令集合，如 '/exit'、'/quit'。

    # ------------------------------------------------------------------
    # 公有接口
    # ------------------------------------------------------------------

    def main(self, argv: list[str] | None = None) -> int:
        """执行 CLI 主入口并返回进程退出码。

        解析命令行参数后，按子命令分流至对应的执行方法。捕获
        ValueError 与 OpenAIClientError 并以退出码 2 返回，
        所有其他异常按调用链透传。

        Args:
            argv (list[str] | None): 命令行参数列表；为 None 时由 argparse 读取进程参数。
        Returns:
            int: 进程退出码。0 表示正常退出，2 表示参数或运行时配置错误。
        """
        parser = self._build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 0)

        try:
            if args.command == 'agent':
                return self._run_agent_command(args)

            if args.command == 'agent-resume':
                return self._run_agent_resume_command(args)

            if args.command == 'agent-chat':
                return self._run_agent_chat_command(args)

            raise ValueError(f'Unknown command: {args.command}')
        except (ValueError, OpenAIClientError) as exc:
            print(f'[main] {exc}', file=sys.stderr)
            return 2

    # ------------------------------------------------------------------
    # 解析器构建
    # ------------------------------------------------------------------

    def _build_parser(self) -> argparse.ArgumentParser:
        """构建 CLI 参数解析器并注册所有子命令。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            argparse.ArgumentParser: 已注册 agent、agent-chat、agent-resume 子命令的解析器。
        """
        parser = argparse.ArgumentParser(description='Run ClawCodeAgent command surface.')
        subparsers = parser.add_subparsers(dest='command')
        subparsers.required = True

        agent_parser = subparsers.add_parser('agent', help='Start an interactive agent session (new session).')
        self._add_common_agent_args(agent_parser)

        chat_parser = subparsers.add_parser('agent-chat', help='Start an interactive agent chat loop (alias for agent).')
        self._add_common_agent_args(chat_parser)
        chat_parser.add_argument('--session-id', default='', help='Resume an existing session inside the chat loop.')

        resume_parser = subparsers.add_parser('agent-resume', help='Resume a saved session interactively.')
        self._add_common_agent_args(resume_parser)
        resume_parser.add_argument('session_id', help='Saved session ID to resume.')

        return parser

    def _add_common_agent_args(self, parser: argparse.ArgumentParser) -> None:
        """为 agent 系列子命令挂载公共参数组。

        Args:
            parser (argparse.ArgumentParser): 目标子命令解析器。
        Returns:
            None: 原地追加参数定义，不返回值。
        """
        self._add_model_args(parser)
        self._add_runtime_args(parser)
        self._add_budget_args(parser)
        self._add_permission_args(parser)

    def _add_model_args(self, parser: argparse.ArgumentParser) -> None:
        """向解析器添加模型相关参数组。

        Args:
            parser (argparse.ArgumentParser): 目标解析器。
        Returns:
            None: 原地追加参数定义，不返回值。
        """
        group = parser.add_argument_group('model')
        group.add_argument('--model', default=None, help='Model name. Fallback: OPENAI_MODEL.')
        group.add_argument('--base-url', default=None, help='OpenAI-compatible base URL. Fallback: OPENAI_BASE_URL.')
        group.add_argument('--api-key', default=None, help='API key. Fallback: OPENAI_API_KEY.')
        group.add_argument('--temperature', type=float, default=None, help='Model temperature override.')
        group.add_argument('--timeout-seconds', type=float, default=None, help='Model request timeout in seconds.')
        group.add_argument(
            '--input-cost-per-million-tokens-usd',
            type=float,
            default=None,
            help='Input token price override for cost estimation.',
        )
        group.add_argument(
            '--output-cost-per-million-tokens-usd',
            type=float,
            default=None,
            help='Output token price override for cost estimation.',
        )
        group.add_argument(
            '--cache-creation-input-cost-per-million-tokens-usd',
            type=float,
            default=None,
            help='Cache write token price override for cost estimation.',
        )
        group.add_argument(
            '--cache-read-input-cost-per-million-tokens-usd',
            type=float,
            default=None,
            help='Cache read token price override for cost estimation.',
        )

    def _add_runtime_args(self, parser: argparse.ArgumentParser) -> None:
        """向解析器添加运行时相关参数组。

        Args:
            parser (argparse.ArgumentParser): 目标解析器。
        Returns:
            None: 原地追加参数定义，不返回值。
        """
        group = parser.add_argument_group('runtime')
        group.add_argument('--cwd', default=None, help='Working directory for tools.')
        group.add_argument('--max-turns', type=int, default=None, help='Maximum agent turns for this command.')
        group.add_argument('--command-timeout-seconds', type=float, default=None, help='Tool command timeout override.')
        group.add_argument('--max-output-chars', type=int, default=None, help='Maximum captured tool output size.')
        group.add_argument(
            '--stream-model-responses',
            action=argparse.BooleanOptionalAction,
            default=None,
            help='Enable or disable streaming model responses.',
        )
        group.add_argument(
            '--show-progress',
            action=argparse.BooleanOptionalAction,
            default=None,
            help='Enable or disable runtime progress logs during interactive turns.',
        )
        group.add_argument('--auto-snip-threshold-tokens', type=int, default=None, help='Auto snip threshold override.')
        group.add_argument('--auto-compact-threshold-tokens', type=int, default=None, help='Auto compact threshold override.')
        group.add_argument('--compact-preserve-messages', type=int, default=None, help='Tail messages preserved during compact/snip.')
        group.add_argument(
            '--additional-working-directory',
            action='append',
            default=None,
            dest='additional_working_directories',
            help='Add an extra working directory. Can be passed multiple times.',
        )
        group.add_argument(
            '--disable-claude-md-discovery',
            action=argparse.BooleanOptionalAction,
            default=None,
            help='Enable or disable Claude.md discovery.',
        )
        group.add_argument('--session-directory', default=None, help='Override the session snapshot directory.')
        group.add_argument('--scratchpad-root', default=None, help='Override the scratchpad root directory.')

    def _add_budget_args(self, parser: argparse.ArgumentParser) -> None:
        """向解析器添加预算约束参数组。

        Args:
            parser (argparse.ArgumentParser): 目标解析器。
        Returns:
            None: 原地追加参数定义，不返回值。
        """
        group = parser.add_argument_group('budget')
        group.add_argument('--max-total-tokens', type=int, default=None)
        group.add_argument('--max-input-tokens', type=int, default=None)
        group.add_argument('--max-output-tokens', type=int, default=None)
        group.add_argument('--max-reasoning-tokens', type=int, default=None)
        group.add_argument('--max-total-cost-usd', type=float, default=None)
        group.add_argument('--max-tool-calls', type=int, default=None)
        group.add_argument('--max-delegated-tasks', type=int, default=None)
        group.add_argument('--max-model-calls', type=int, default=None)
        group.add_argument('--max-session-turns', type=int, default=None)

    def _add_permission_args(self, parser: argparse.ArgumentParser) -> None:
        """向解析器添加权限开关参数组。

        Args:
            parser (argparse.ArgumentParser): 目标解析器。
        Returns:
            None: 原地追加参数定义，不返回值。
        """
        group = parser.add_argument_group('permissions')
        group.add_argument(
            '--allow-file-write',
            action=argparse.BooleanOptionalAction,
            default=None,
            help='Enable or disable write_file/edit_file permissions.',
        )
        group.add_argument(
            '--allow-shell',
            action=argparse.BooleanOptionalAction,
            default=None,
            help='Enable or disable shell command permissions.',
        )
        group.add_argument(
            '--allow-destructive-shell',
            action=argparse.BooleanOptionalAction,
            default=None,
            help='Enable or disable destructive shell permissions.',
        )

    # ------------------------------------------------------------------
    # agent 子命令：新会话
    # ------------------------------------------------------------------

    def _run_agent_command(self, args: argparse.Namespace) -> int:
        """执行 agent 子命令，构造新会话并进入交互循环。

        Args:
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            int: 交互循环结束后的进程退出码。
        Raises:
            ValueError: 当运行时配置约束非法时抛出。
            OpenAIClientError: 当模型客户端初始化失败时透传。
        """
        agent, runtime_config = self._build_agent_from_args(args)
        current_session_directory = (
            self._normalize_optional_path(args.session_directory)
            or runtime_config.session_directory.resolve()
        )
        return self._run_interactive_loop(
            agent,
            current_session_id=None,
            current_session_directory=current_session_directory,
            pending_session_snapshot=None,
            show_progress=self._resolve_show_progress(args),
        )

    def _build_agent_from_args(self, args: argparse.Namespace) -> tuple[LocalAgent, AgentRuntimeConfig]:
        """根据命令行参数构造新会话代理实例。

        Args:
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            tuple[LocalAgent, AgentRuntimeConfig]: 新建的代理实例与其运行时配置。
        Raises:
            ValueError: 当必填配置字段缺失或运行时配置约束非法时抛出。
            OpenAIClientError: 当模型客户端初始化失败时透传。
        """
        model_config = self._build_new_model_config(args)
        runtime_config = self._build_new_runtime_config(args)
        self._validate_runtime_config(runtime_config)
        client = self._openai_client_cls(model_config)
        session_store = self._session_store_cls(runtime_config.session_directory)
        return self._agent_cls(client, runtime_config, session_store), runtime_config

    def _build_new_model_config(self, args: argparse.Namespace) -> ModelConfig:
        """构建新会话使用的模型配置。

        按优先级合并命令行参数、环境变量与默认值后返回完整 ModelConfig。

        Args:
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            ModelConfig: 合并后的模型配置实例。
        Raises:
            ValueError: 当 --model 或 --api-key 既未通过 CLI 传入也未在环境变量中设置时抛出。
        """
        base_url = (
            self._normalize_optional_text(args.base_url)
            or os.getenv('OPENAI_BASE_URL', '').strip()
            or 'http://127.0.0.1:8000/v1'
        )
        base_model = ModelConfig(
            model=self._required_value(args.model, 'OPENAI_MODEL', 'model'),
            base_url=base_url,
            api_key=self._required_value(args.api_key, 'OPENAI_API_KEY', 'api_key'),
        )
        return self._apply_model_overrides(base_model, args)

    def _required_value(self, cli_value: str | None, env_key: str, field_name: str) -> str:
        """从命令行参数或环境变量中读取必填文本值。

        Args:
            cli_value (str | None): 命令行显式传入的值；可为 None。
            env_key (str): 回退环境变量的键名。
            field_name (str): 业务字段名，用于生成报错文案。
        Returns:
            str: 成功解析到的非空字符串。
        Raises:
            ValueError: 当命令行与环境变量均未提供有效值时抛出。
        """
        normalized = self._normalize_optional_text(cli_value)
        if normalized:
            return normalized

        env_value = os.getenv(env_key, '').strip()
        if env_value:
            return env_value
        raise ValueError(f'Missing required {field_name}. Use --{field_name.replace("_", "-")} or {env_key}.')

    def _normalize_optional_text(self, value: str | None) -> str | None:
        """清洗可选文本，把空白值折叠为 None。

        Args:
            value (str | None): 原始文本值。
        Returns:
            str | None: 去除首尾空白后的文本；若结果为空字符串则返回 None。
        """
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _apply_model_overrides(self, base_model: ModelConfig, args: argparse.Namespace) -> ModelConfig:
        """把命令行中的模型覆盖项合并到基线模型配置。

        Args:
            base_model (ModelConfig): 基线模型配置。
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            ModelConfig: 合并后的模型配置；若无任何覆盖项则返回原对象。
        """
        updates: dict[str, object] = {}
        model = self._normalize_optional_text(args.model)
        base_url = self._normalize_optional_text(args.base_url)
        api_key = self._normalize_optional_text(args.api_key)
        if model is not None:
            updates['model'] = model
        if base_url is not None:
            updates['base_url'] = base_url
        if api_key is not None:
            updates['api_key'] = api_key
        if args.temperature is not None:
            updates['temperature'] = args.temperature
        if args.timeout_seconds is not None:
            updates['timeout_seconds'] = args.timeout_seconds

        pricing = self._apply_pricing_overrides(base_model.pricing, args)
        if pricing != base_model.pricing:
            updates['pricing'] = pricing

        if not updates:
            return base_model
        return replace(base_model, **updates)

    def _apply_pricing_overrides(self, base_pricing: ModelPricing, args: argparse.Namespace) -> ModelPricing:
        """把计费相关命令行参数覆盖到基线计费配置。

        Args:
            base_pricing (ModelPricing): 基线计费配置。
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            ModelPricing: 合并后的计费配置；若无覆盖项则返回原对象。
        """
        updates: dict[str, float] = {}
        for field_name in (
            'input_cost_per_million_tokens_usd',
            'output_cost_per_million_tokens_usd',
            'cache_creation_input_cost_per_million_tokens_usd',
            'cache_read_input_cost_per_million_tokens_usd',
        ):
            value = getattr(args, field_name)
            if value is not None:
                updates[field_name] = value
        if not updates:
            return base_pricing
        return replace(base_pricing, **updates)

    def _build_new_runtime_config(self, args: argparse.Namespace) -> AgentRuntimeConfig:
        """构建新会话使用的运行时配置。

        Args:
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            AgentRuntimeConfig: 合并命令行参数后的运行时配置实例。
        """
        runtime_config = AgentRuntimeConfig(
            cwd=self._normalize_optional_path(args.cwd) or Path('.').resolve()
        )
        return self._apply_runtime_overrides(runtime_config, args)

    def _normalize_optional_path(self, value: str | None) -> Path | None:
        """清洗可选路径文本并解析为绝对路径。

        Args:
            value (str | None): 原始路径文本。
        Returns:
            Path | None: 解析后的绝对 Path 对象；若输入为空则返回 None。
        """
        normalized = self._normalize_optional_text(value)
        if normalized is None:
            return None
        return Path(normalized).resolve()

    def _apply_runtime_overrides(self, base_runtime: AgentRuntimeConfig, args: argparse.Namespace) -> AgentRuntimeConfig:
        """把命令行中的运行时覆盖项合并到基线运行时配置。

        Args:
            base_runtime (AgentRuntimeConfig): 基线运行时配置。
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            AgentRuntimeConfig: 合并后的运行时配置实例。
        """
        permissions = self._apply_permission_overrides(base_runtime.permissions, args)
        budget_config = self._apply_budget_overrides(base_runtime.budget_config, args)

        updates: dict[str, object] = {
            'permissions': permissions,
            'budget_config': budget_config,
        }

        cwd = self._normalize_optional_path(args.cwd)
        session_directory = self._normalize_optional_path(args.session_directory)
        scratchpad_root = self._normalize_optional_path(args.scratchpad_root)
        additional_dirs = self._normalize_optional_paths(getattr(args, 'additional_working_directories', None))

        if cwd is not None:
            updates['cwd'] = cwd
        if args.max_turns is not None:
            updates['max_turns'] = args.max_turns
        if args.command_timeout_seconds is not None:
            updates['command_timeout_seconds'] = args.command_timeout_seconds
        if args.max_output_chars is not None:
            updates['max_output_chars'] = args.max_output_chars
        if args.stream_model_responses is not None:
            updates['stream_model_responses'] = args.stream_model_responses
        if args.auto_snip_threshold_tokens is not None:
            updates['auto_snip_threshold_tokens'] = args.auto_snip_threshold_tokens
        if args.auto_compact_threshold_tokens is not None:
            updates['auto_compact_threshold_tokens'] = args.auto_compact_threshold_tokens
        if args.compact_preserve_messages is not None:
            updates['compact_preserve_messages'] = args.compact_preserve_messages
        if additional_dirs is not None:
            updates['additional_working_directories'] = additional_dirs
        if args.disable_claude_md_discovery is not None:
            updates['disable_claude_md_discovery'] = args.disable_claude_md_discovery
        if session_directory is not None:
            updates['session_directory'] = session_directory
        if scratchpad_root is not None:
            updates['scratchpad_root'] = scratchpad_root

        return replace(base_runtime, **updates)

    def _apply_permission_overrides(self, base_permissions: AgentPermissions, args: argparse.Namespace) -> AgentPermissions:
        """把权限开关参数覆盖到基线权限配置。

        Args:
            base_permissions (AgentPermissions): 基线权限配置。
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            AgentPermissions: 合并后的权限配置；若无覆盖项则返回原对象。
        """
        updates: dict[str, bool] = {}
        if args.allow_file_write is not None:
            updates['allow_file_write'] = args.allow_file_write
        if args.allow_shell is not None:
            updates['allow_shell_commands'] = args.allow_shell
        if args.allow_destructive_shell is not None:
            updates['allow_destructive_shell_commands'] = args.allow_destructive_shell
        if not updates:
            return base_permissions
        return replace(base_permissions, **updates)

    def _apply_budget_overrides(self, base_budget: BudgetConfig, args: argparse.Namespace) -> BudgetConfig:
        """把预算约束参数覆盖到基线预算配置。

        Args:
            base_budget (BudgetConfig): 基线预算配置。
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            BudgetConfig: 合并后的预算配置；若无覆盖项则返回原对象。
        """
        updates: dict[str, int | float] = {}
        for field_name in (
            'max_total_tokens',
            'max_input_tokens',
            'max_output_tokens',
            'max_reasoning_tokens',
            'max_total_cost_usd',
            'max_tool_calls',
            'max_delegated_tasks',
            'max_model_calls',
            'max_session_turns',
        ):
            value = getattr(args, field_name)
            if value is not None:
                updates[field_name] = value
        if not updates:
            return base_budget
        return replace(base_budget, **updates)

    def _normalize_optional_paths(self, values: list[str] | None) -> tuple[Path, ...] | None:
        """把可选路径字符串列表解析为绝对路径元组。

        Args:
            values (list[str] | None): 原始路径字符串列表；可为 None。
        Returns:
            tuple[Path, ...] | None: 解析后的绝对路径元组；若输入为 None 则返回 None。
        """
        if values is None:
            return None
        normalized = [Path(item).resolve() for item in values if isinstance(item, str) and item.strip()]
        return tuple(normalized)

    def _validate_runtime_config(self, runtime_config: AgentRuntimeConfig) -> None:
        """校验运行时配置的跨字段约束。

        Args:
            runtime_config (AgentRuntimeConfig): 待校验的运行时配置。
        Returns:
            None: 校验通过时不返回值。
        Raises:
            ValueError: 当 destructive shell 已开启但 shell 总开关未开启时抛出。
        """
        permissions = runtime_config.permissions
        if permissions.allow_destructive_shell_commands and not permissions.allow_shell_commands:
            raise ValueError('allow_destructive_shell requires --allow-shell')

    # ------------------------------------------------------------------
    # 交互循环：执行、渲染、状态推进
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_show_progress(args: argparse.Namespace) -> bool:
        """解析交互式 progress 输出开关。

        Args:
            args (argparse.Namespace): 已解析的命令行参数。
        Returns:
            bool: 未显式指定时默认返回 True，否则返回 show_progress 的布尔值。
        """
        if args.show_progress is None:
            return True
        return bool(args.show_progress)

    def _run_interactive_loop(
        self,
        agent: LocalAgent,
        *,
        current_session_id: str | None,
        current_session_directory: Path | None,
        pending_session_snapshot: AgentSessionSnapshot | None,
        show_progress: bool,
    ) -> int:
        """执行通用多轮交互循环，供三条子命令共用。

        每轮读取用户输入后自动选择 run 或 resume 路径，渲染结果并更新
        会话状态；遇到退出命令、EOF 或 KeyboardInterrupt 时正常终止。

        Args:
            agent (LocalAgent): 当前命令对应的代理实例。
            current_session_id (str | None): 当前活动会话 ID；新会话场景下为 None。
            current_session_directory (Path | None): 当前会话快照所在目录；可为 None。
            pending_session_snapshot (AgentSessionSnapshot | None): 已预加载但尚未消费的快照。
            show_progress (bool): 是否启用运行期 progress 日志与动态状态栏。
        Returns:
            int: 交互循环退出码；用户主动退出、EOF、KeyboardInterrupt 均返回 0。
        """
        self._banner_renderer.render()
        progress_printer = RuntimeEventPrinter() if show_progress else None
        self._configure_agent_progress(agent, progress_printer)
        interaction_tracker = SessionInteractionTracker.start(current_session_id)
        while True:
            try:
                prompt = input('agent> ')
            except EOFError:
                self._flush_progress_printer(progress_printer)
                return self._finalize_interactive_loop(interaction_tracker, leading_blank_line=True)
            except KeyboardInterrupt:
                self._flush_progress_printer(progress_printer)
                return self._finalize_interactive_loop(interaction_tracker, leading_blank_line=True)

            normalized = prompt.strip()
            if not normalized:
                continue
            if normalized in self._chat_exit_commands:
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
            self._render_chat_result(result, previous_session_id=current_session_id)
            current_session_id, current_session_directory = self._advance_chat_state(
                result,
                current_session_id=current_session_id,
                current_session_directory=current_session_directory,
            )
            interaction_tracker.observe_run_result(
                result,
                current_session_id=current_session_id,
            )
            print()  # turn separator, 每轮结束后输出一个空行分隔

    @staticmethod
    def _configure_agent_progress(
        agent: object,
        progress_printer: RuntimeEventPrinter | None,
    ) -> None:
        """为当前 agent 动态挂载 progress reporter。

        Args:
            agent (object): 需要被挂载 progress_reporter 属性的代理对象。
            progress_printer (RuntimeEventPrinter | None): 负责消费结构化事件的打印器；为空时清空 reporter。
        Returns:
            None: 该方法只修改 agent 对象上的 progress_reporter 属性。
        """
        reporter = progress_printer.emit if progress_printer is not None else None
        setattr(agent, 'progress_reporter', reporter)

    @staticmethod
    def _flush_progress_printer(progress_printer: RuntimeEventPrinter | None) -> None:
        """输出 progress printer 中尚未刷新的残留片段。

        Args:
            progress_printer (RuntimeEventPrinter | None): 待冲刷的 progress 打印器。
        Returns:
            None: 该方法只在打印器存在时触发 flush。
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
            tracker (SessionInteractionTracker): 当前交互期间的累计统计追踪器。
            leading_blank_line (bool): 是否在提示框前额外输出一个空行。
        Returns:
            int: 固定返回 0，表示交互循环正常结束。
        """
        if leading_blank_line:
            print()
        self._exit_summary_renderer.render(tracker.to_summary())
        return 0

    def _execute_chat_turn(
        self,
        agent: LocalAgent,
        *,
        prompt: str,
        current_session_id: str | None,
        current_session_directory: Path | None,
        session_snapshot: AgentSessionSnapshot | None,
    ) -> AgentRunResult:
        """执行单轮聊天输入，在 run 与 resume 之间自动分流。

        若存在活动会话 ID 则走 resume 路径（必要时重新从磁盘加载快照），
        否则走 run 路径发起全新对话。

        Args:
            agent (LocalAgent): 当前代理实例。
            prompt (str): 用户本轮输入文本。
            current_session_id (str | None): 当前活动会话 ID。
            current_session_directory (Path | None): 当前会话快照所在目录。
            session_snapshot (AgentSessionSnapshot | None): 可复用的已加载会话快照。
        Returns:
            AgentRunResult: 本轮执行结果。
        Raises:
            ValueError: 当恢复会话快照加载失败时透传。
            OpenAIClientError: 当底层模型客户端执行失败时透传。
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
            session_id (str): 待加载的会话 ID。
            directory (Path | None): 会话快照目录；为 None 时由存储实现使用默认位置。
        Returns:
            AgentSessionSnapshot: 已加载的会话快照对象。
        Raises:
            ValueError: 当会话不存在或快照内容非法时透传。
        """
        session_store = self._session_store_cls(directory)
        return session_store.load(session_id)

    def _render_chat_result(self, result: AgentRunResult, *, previous_session_id: str | None) -> None:
        """把单轮执行结果渲染到标准输出。

        Args:
            result (AgentRunResult): 当前轮的执行结果。
            previous_session_id (str | None): 上一轮已知的会话 ID，用于判断是否需要打印新会话行。
        Returns:
            None: 该方法只负责输出，不返回值。
        """
        if result.final_output:
            print(result.final_output)
        elif fallback_message := self._derive_empty_result_message(result):
            print(fallback_message)
        # if result.session_id and result.session_id != previous_session_id:
        #     print(f'[session] {result.session_id}')

    @staticmethod
    def _derive_empty_result_message(result: AgentRunResult) -> str | None:
        """当 final_output 为空时，从 stop_reason 或 events 里提取可读诊断信息。

        Args:
            result (AgentRunResult): 当前轮的执行结果。
        Returns:
            str | None: 找到可读诊断文本时返回消息，否则返回 None。
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
            result (AgentRunResult): 当前轮执行结果。
            current_session_id (str | None): 本轮前的活动会话 ID。
            current_session_directory (Path | None): 本轮前的会话快照目录。
        Returns:
            tuple[str | None, Path | None]: 更新后的会话 ID 与快照目录。
        """
        next_session_id = result.session_id or current_session_id
        next_directory = current_session_directory
        if result.session_path:
            next_directory = Path(result.session_path).resolve().parent
        return next_session_id, next_directory

    # ------------------------------------------------------------------
    # agent-resume 子命令：恢复会话
    # ------------------------------------------------------------------

    def _run_agent_resume_command(self, args: argparse.Namespace) -> int:
        """执行 agent-resume 子命令，加载存档会话并进入交互循环。

        Args:
            args (argparse.Namespace): 已解析的命令行参数；args.session_id 为必填位置参数。
        Returns:
            int: 交互循环结束后的进程退出码。
        Raises:
            ValueError: 当会话不存在或运行时配置约束非法时抛出。
            OpenAIClientError: 当模型客户端初始化失败时透传。
        """
        agent, pending_session_snapshot, current_session_directory = self._build_resumed_agent(
            args,
            session_id=args.session_id,
        )
        return self._run_interactive_loop(
            agent,
            current_session_id=args.session_id,
            current_session_directory=current_session_directory,
            pending_session_snapshot=pending_session_snapshot,
            show_progress=self._resolve_show_progress(args),
        )

    def _build_resumed_agent(
        self,
        args: argparse.Namespace,
        *,
        session_id: str,
    ) -> tuple[LocalAgent, AgentSessionSnapshot, Path | None]:
        """根据持久化会话快照构造恢复态代理。

        从存储中加载快照后，以命令行参数对快照中的模型配置与运行时配置做覆盖，
        再据此创建客户端、存储与代理实例。

        Args:
            args (argparse.Namespace): 已解析的命令行参数（用于覆盖快照内配置）。
            session_id (str): 待恢复的会话 ID。
        Returns:
            tuple[LocalAgent, AgentSessionSnapshot, Path | None]:
                恢复态代理实例、已加载快照、当前会话目录（可为 None）。
        Raises:
            ValueError: 当会话不存在或运行时配置约束非法时抛出。
            OpenAIClientError: 当模型客户端初始化失败时透传。
        """
        loader_directory = self._normalize_optional_path(args.session_directory)
        session_snapshot = self._load_session_snapshot(
            session_id,
            directory=loader_directory,
        )
        model_config = self._apply_model_overrides(session_snapshot.model_config, args)
        runtime_config = self._apply_runtime_overrides(session_snapshot.runtime_config, args)
        self._validate_runtime_config(runtime_config)
        client = self._openai_client_cls(model_config)
        session_store = self._session_store_cls(runtime_config.session_directory)
        return (
            self._agent_cls(client, runtime_config, session_store),
            session_snapshot,
            loader_directory or runtime_config.session_directory.resolve(),
        )

    # ------------------------------------------------------------------
    # agent-chat 子命令：可选恢复的交互会话
    # ------------------------------------------------------------------

    def _run_agent_chat_command(self, args: argparse.Namespace) -> int:
        """执行 agent-chat 子命令，在新会话与恢复会话之间按 --session-id 自动切换。

        Args:
            args (argparse.Namespace): 已解析的命令行参数；
                args.session_id 非空时进入恢复态，否则构造新会话。
        Returns:
            int: 交互循环结束后的进程退出码。
        Raises:
            ValueError: 当恢复会话失败或运行时配置约束非法时抛出。
            OpenAIClientError: 当模型客户端初始化失败时透传。
        """
        current_session_id = self._normalize_optional_text(args.session_id)
        current_session_directory = self._normalize_optional_path(args.session_directory)
        pending_session_snapshot: AgentSessionSnapshot | None = None

        if current_session_id:
            agent, pending_session_snapshot, current_session_directory = self._build_resumed_agent(
                args,
                session_id=current_session_id,
            )
        else:
            agent, runtime_config = self._build_agent_from_args(args)
            current_session_directory = current_session_directory or runtime_config.session_directory.resolve()

        return self._run_interactive_loop(
            agent,
            current_session_id=current_session_id,
            current_session_directory=current_session_directory,
            pending_session_snapshot=pending_session_snapshot,
            show_progress=self._resolve_show_progress(args),
        )

    # ------------------------------------------------------------------
    # 通用底层工具
    # ------------------------------------------------------------------

    def _join_prompt_parts(self, parts: list[str]) -> str:
        """把提示词片段列表拼接为单个空格分隔的字符串。

        Args:
            parts (list[str]): 原始提示词片段列表；空白片段会被过滤。
        Returns:
            str: 去除空白片段后的拼接结果。
        """
        return ' '.join(part.strip() for part in parts if part.strip())

    def _join_optional_prompt_parts(self, parts: list[str]) -> str | None:
        """把提示词片段拼接为可选字符串，全空时返回 None。

        Args:
            parts (list[str]): 原始提示词片段列表。
        Returns:
            str | None: 拼接后的提示词；若结果为空字符串则返回 None。
        """
        prompt = self._join_prompt_parts(parts)
        return prompt or None


