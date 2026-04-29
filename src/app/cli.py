"""控制面命令行交互模块。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.chat_loop import ChatLoop
from app.runtime_builder import RuntimeBuilder
from core_contracts.run_result import AgentRunResult
from interaction.quit_render import ExitRenderer
from interaction.slash_render import SlashCommandRenderer
from interaction.startup_render import StartupRenderer
from openai_client.openai_client import OpenAIClient, OpenAIClientError
from session import AgentSessionSnapshot, SessionManager


class AppCLI:
    """控制面命令行交互协调器。"""

    _DEFAULT_CHAT_EXIT_COMMANDS: frozenset[str] = frozenset({'/exit', '/quit'})

    def __init__(
        self,
        *,
        openai_client_cls: type[OpenAIClient] = OpenAIClient,
        agent_cls,
        session_manager_cls: type[SessionManager] = SessionManager,
        startup_renderer: StartupRenderer | None = None,
        exit_renderer: ExitRenderer | None = None,
        slash_renderer: SlashCommandRenderer | None = None,
        chat_exit_commands: frozenset[str] | None = None,
    ) -> None:
        self._runtime_builder = RuntimeBuilder(
            openai_client_cls=openai_client_cls,
            agent_cls=agent_cls,
            session_manager_cls=session_manager_cls,
        )
        self._chat_loop = ChatLoop(
            session_manager_cls=session_manager_cls,
            startup_renderer=startup_renderer or StartupRenderer(),
            exit_renderer=exit_renderer or ExitRenderer(),
            slash_renderer=slash_renderer or SlashCommandRenderer(),
            chat_exit_commands=chat_exit_commands or self._DEFAULT_CHAT_EXIT_COMMANDS,
        )

    def main(self, argv: list[str] | None = None) -> int:
        """执行 CLI 主入口并返回进程退出码。"""
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

    def _build_parser(self) -> argparse.ArgumentParser:
        """构建 CLI 参数解析器并注册所有子命令。"""
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
        """为 agent 系列子命令挂载公共参数组。"""
        self._add_model_args(parser)
        self._add_runtime_args(parser)
        self._add_budget_args(parser)
        self._add_permission_args(parser)

    def _add_model_args(self, parser: argparse.ArgumentParser) -> None:
        """向解析器添加模型相关参数组。"""
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
        """向解析器添加运行时相关参数组。"""
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
        """向解析器添加预算约束参数组。"""
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
        """向解析器添加权限开关参数组。"""
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

    def _run_agent_command(self, args: argparse.Namespace) -> int:
        """执行 agent 子命令，构造新会话并进入交互循环。"""
        agent, session_paths = self._runtime_builder.build_agent_from_args(args)
        current_session_directory = (
            self._normalize_optional_path(args.session_directory)
            or session_paths.session_directory.resolve()
        )
        return self._chat_loop.run(
            agent,
            current_session_id=None,
            current_session_directory=current_session_directory,
            pending_session_snapshot=None,
            show_progress=RuntimeBuilder.resolve_show_progress(args),
        )

    def _run_agent_resume_command(self, args: argparse.Namespace) -> int:
        """执行 agent-resume 子命令，加载存档会话并进入交互循环。"""
        agent, pending_session_snapshot, current_session_directory = self._runtime_builder.build_resumed_agent(
            args,
            session_id=args.session_id,
        )
        return self._chat_loop.run(
            agent,
            current_session_id=args.session_id,
            current_session_directory=current_session_directory,
            pending_session_snapshot=pending_session_snapshot,
            show_progress=RuntimeBuilder.resolve_show_progress(args),
        )

    def _run_agent_chat_command(self, args: argparse.Namespace) -> int:
        """执行 agent-chat 子命令，在新会话与恢复会话间按 --session-id 自动切换。"""
        current_session_id = self._normalize_optional_text(args.session_id)
        current_session_directory = self._normalize_optional_path(args.session_directory)
        pending_session_snapshot: AgentSessionSnapshot | None = None

        if current_session_id:
            agent, pending_session_snapshot, current_session_directory = self._runtime_builder.build_resumed_agent(
                args,
                session_id=current_session_id,
            )
        else:
            agent, session_paths = self._runtime_builder.build_agent_from_args(args)
            current_session_directory = current_session_directory or session_paths.session_directory.resolve()

        return self._chat_loop.run(
            agent,
            current_session_id=current_session_id,
            current_session_directory=current_session_directory,
            pending_session_snapshot=pending_session_snapshot,
            show_progress=RuntimeBuilder.resolve_show_progress(args),
        )

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        """清洗可选文本，把空白值折叠为 None。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _normalize_optional_path(self, value: str | None) -> Path | None:
        """清洗可选路径文本并解析为绝对路径。"""
        normalized = self._normalize_optional_text(value)
        if normalized is None:
            return None
        return Path(normalized).resolve()


def main(argv: list[str] | None = None) -> int:
    """模块级入口，便于外部直接调用 app CLI。"""
    from agent import Agent

    cli = AppCLI(agent_cls=Agent)
    return cli.main(argv)
