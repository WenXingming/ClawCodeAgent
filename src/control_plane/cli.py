"""控制面 CLI 实现。"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from control_plane.startup_banner import StartupBannerRenderer
from core_contracts.config import AgentPermissions, AgentRuntimeConfig, BudgetConfig, ModelConfig
from core_contracts.result import AgentRunResult
from core_contracts.usage import ModelPricing
from openai_client.openai_client import OpenAIClient, OpenAIClientError
from orchestration.agent_runtime import LocalCodingAgent
from session.session_snapshot import AgentSessionSnapshot
from session.session_store import AgentSessionStore


_CHAT_EXIT_COMMANDS = {'.exit', '.quit'}
_STARTUP_BANNER = StartupBannerRenderer()


def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器并注册子命令。

    Returns:
        argparse.ArgumentParser: 已完成子命令与参数组注册的解析器。
    """
    parser = argparse.ArgumentParser(description='Run ClawCodeAgent command surface.')
    subparsers = parser.add_subparsers(dest='command')
    subparsers.required = True

    agent_parser = subparsers.add_parser('agent', help='Start an interactive agent session (new session).')
    _add_common_agent_args(agent_parser)

    chat_parser = subparsers.add_parser('agent-chat', help='Start an interactive agent chat loop (alias for agent).')
    _add_common_agent_args(chat_parser)
    chat_parser.add_argument('--session-id', default='', help='Resume an existing session inside the chat loop.')

    resume_parser = subparsers.add_parser('agent-resume', help='Resume a saved session interactively.')
    _add_common_agent_args(resume_parser)
    resume_parser.add_argument('session_id', help='Saved session ID to resume.')

    return parser


def _add_common_agent_args(parser: argparse.ArgumentParser) -> None:
    """为 agent 系列命令挂载公共参数组。

    Args:
        parser (argparse.ArgumentParser): 目标解析器。

    Returns:
        None: 原地追加参数定义。
    """
    _add_model_args(parser)
    _add_runtime_args(parser)
    _add_budget_args(parser)
    _add_permission_args(parser)


def _add_model_args(parser: argparse.ArgumentParser) -> None:
    """添加模型相关参数。

    Args:
        parser (argparse.ArgumentParser): 目标解析器。
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


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    """添加运行时相关参数。"""
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


def _add_budget_args(parser: argparse.ArgumentParser) -> None:
    """添加预算约束参数。"""
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


def _add_permission_args(parser: argparse.ArgumentParser) -> None:
    """添加权限开关参数。"""
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


def _required_value(cli_value: str | None, env_key: str, field_name: str) -> str:
    """从命令行参数或环境变量中读取必填值。"""
    normalized = _normalize_optional_text(cli_value)
    if normalized:
        return normalized

    env_value = os.getenv(env_key, '').strip()
    if env_value:
        return env_value
    raise ValueError(f'Missing required {field_name}. Use --{field_name.replace("_", "-")} or {env_key}.')


def main(
    argv: list[str] | None = None,
    *,
    openai_client_cls: type[OpenAIClient] = OpenAIClient,
    agent_cls: type[LocalCodingAgent] = LocalCodingAgent,
    session_store_cls: type[AgentSessionStore] = AgentSessionStore,
) -> int:
    """执行 `main` 逻辑。
    Args:
        argv (list[str] | None): 参数 `argv`。
        openai_client_cls (type[OpenAIClient]): 参数 `openai_client_cls`。
        agent_cls (type[LocalCodingAgent]): 参数 `agent_cls`。
        session_store_cls (type[AgentSessionStore]): 参数 `session_store_cls`。
    Returns:
        int: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        if args.command == 'agent':
            return _run_agent_command(
                args,
                openai_client_cls=openai_client_cls,
                agent_cls=agent_cls,
                session_store_cls=session_store_cls,
            )

        if args.command == 'agent-resume':
            return _run_agent_resume_command(
                args,
                openai_client_cls=openai_client_cls,
                agent_cls=agent_cls,
                session_store_cls=session_store_cls,
            )

        if args.command == 'agent-chat':
            return _run_agent_chat_command(
                args,
                openai_client_cls=openai_client_cls,
                agent_cls=agent_cls,
                session_store_cls=session_store_cls,
            )

        raise ValueError(f'Unknown command: {args.command}')
    except (ValueError, OpenAIClientError) as exc:
        print(f'[main] {exc}', file=sys.stderr)
        return 2


def _run_agent_command(
    args: argparse.Namespace,
    *,
    openai_client_cls: type[OpenAIClient],
    agent_cls: type[LocalCodingAgent],
    session_store_cls: type[AgentSessionStore],
) -> int:
    """执行 `agent` 子命令：构造新会话并进入交互循环。"""
    agent, runtime_config = _build_agent_from_args(
        args,
        openai_client_cls=openai_client_cls,
        agent_cls=agent_cls,
        session_store_cls=session_store_cls,
    )
    current_session_directory = _normalize_optional_path(args.session_directory) or runtime_config.session_directory.resolve()
    return _run_interactive_loop(
        agent,
        current_session_id=None,
        current_session_directory=current_session_directory,
        pending_session_snapshot=None,
        session_store_cls=session_store_cls,
    )


def _run_agent_resume_command(
    args: argparse.Namespace,
    *,
    openai_client_cls: type[OpenAIClient],
    agent_cls: type[LocalCodingAgent],
    session_store_cls: type[AgentSessionStore],
) -> int:
    """执行 `agent-resume` 子命令：加载存档会话并进入交互循环。"""
    agent, pending_session_snapshot, current_session_directory = _build_resumed_agent(
        args,
        session_id=args.session_id,
        openai_client_cls=openai_client_cls,
        agent_cls=agent_cls,
        session_store_cls=session_store_cls,
    )
    return _run_interactive_loop(
        agent,
        current_session_id=args.session_id,
        current_session_directory=current_session_directory,
        pending_session_snapshot=pending_session_snapshot,
        session_store_cls=session_store_cls,
    )


def _run_agent_chat_command(
    args: argparse.Namespace,
    *,
    openai_client_cls: type[OpenAIClient],
    agent_cls: type[LocalCodingAgent],
    session_store_cls: type[AgentSessionStore],
) -> int:
    """执行 `agent-chat` 子命令（与 agent/agent-resume 共用同一交互循环）。"""
    current_session_id = _normalize_optional_text(args.session_id)
    current_session_directory = _normalize_optional_path(args.session_directory)
    pending_session_snapshot: AgentSessionSnapshot | None = None

    if current_session_id:
        agent, pending_session_snapshot, current_session_directory = _build_resumed_agent(
            args,
            session_id=current_session_id,
            openai_client_cls=openai_client_cls,
            agent_cls=agent_cls,
            session_store_cls=session_store_cls,
        )
    else:
        agent, runtime_config = _build_agent_from_args(
            args,
            openai_client_cls=openai_client_cls,
            agent_cls=agent_cls,
            session_store_cls=session_store_cls,
        )
        current_session_directory = current_session_directory or runtime_config.session_directory.resolve()

    return _run_interactive_loop(
        agent,
        current_session_id=current_session_id,
        current_session_directory=current_session_directory,
        pending_session_snapshot=pending_session_snapshot,
        session_store_cls=session_store_cls,
    )


def _run_interactive_loop(
    agent: LocalCodingAgent,
    *,
    current_session_id: str | None,
    current_session_directory: Path | None,
    pending_session_snapshot: AgentSessionSnapshot | None,
    session_store_cls: type[AgentSessionStore],
) -> int:
    """通用多轮交互循环，供 agent / agent-resume / agent-chat 共用。"""
    _STARTUP_BANNER.render()
    while True:
        try:
            prompt = input('agent> ')
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0

        normalized = prompt.strip()
        if not normalized:
            continue
        if normalized in _CHAT_EXIT_COMMANDS:
            return 0

        result = _execute_chat_turn(
            agent,
            prompt=prompt,
            current_session_id=current_session_id,
            current_session_directory=current_session_directory,
            session_snapshot=pending_session_snapshot,
            session_store_cls=session_store_cls,
        )
        pending_session_snapshot = None
        _render_chat_result(result, previous_session_id=current_session_id)
        current_session_id, current_session_directory = _advance_chat_state(
            result,
            current_session_id=current_session_id,
            current_session_directory=current_session_directory,
        )


def _execute_chat_turn(
    agent: LocalCodingAgent,
    *,
    prompt: str,
    current_session_id: str | None,
    current_session_directory: Path | None,
    session_snapshot: AgentSessionSnapshot | None,
    session_store_cls: type[AgentSessionStore],
) -> AgentRunResult:
    """执行单轮 chat 输入，自动分流 run/resume。"""
    if current_session_id:
        effective_snapshot = session_snapshot
        if effective_snapshot is None or effective_snapshot.session_id != current_session_id:
            effective_snapshot = _load_session_snapshot(
                current_session_id,
                directory=current_session_directory,
                session_store_cls=session_store_cls,
            )
        return agent.resume(prompt, effective_snapshot)
    return agent.run(prompt)


def _render_chat_result(result: AgentRunResult, *, previous_session_id: str | None) -> None:
    """渲染单轮聊天结果到标准输出。"""
    if result.final_output:
        print(result.final_output)
    if result.session_id and result.session_id != previous_session_id:
        print(f'[session] {result.session_id}')


def _advance_chat_state(
    result: AgentRunResult,
    *,
    current_session_id: str | None,
    current_session_directory: Path | None,
) -> tuple[str | None, Path | None]:
    """根据本轮结果推进 session_id 与目录状态。"""
    next_session_id = result.session_id or current_session_id
    next_directory = current_session_directory
    if result.session_path:
        next_directory = Path(result.session_path).resolve().parent
    return next_session_id, next_directory


def _build_agent_from_args(
    args: argparse.Namespace,
    *,
    openai_client_cls: type[OpenAIClient],
    agent_cls: type[LocalCodingAgent],
    session_store_cls: type[AgentSessionStore],
) -> tuple[LocalCodingAgent, AgentRuntimeConfig]:
    """根据命令参数构造新会话 Agent。"""
    model_config = _build_new_model_config(args)
    runtime_config = _build_new_runtime_config(args)
    _validate_runtime_config(runtime_config)
    client = openai_client_cls(model_config)
    session_store = session_store_cls(runtime_config.session_directory)
    return agent_cls(client, runtime_config, session_store), runtime_config


def _build_resumed_agent(
    args: argparse.Namespace,
    *,
    session_id: str,
    openai_client_cls: type[OpenAIClient],
    agent_cls: type[LocalCodingAgent],
    session_store_cls: type[AgentSessionStore],
) -> tuple[LocalCodingAgent, AgentSessionSnapshot, Path | None]:
    """根据会话快照构造恢复态 Agent。"""
    loader_directory = _normalize_optional_path(args.session_directory)
    session_snapshot = _load_session_snapshot(
        session_id,
        directory=loader_directory,
        session_store_cls=session_store_cls,
    )
    model_config = _apply_model_overrides(session_snapshot.model_config, args)
    runtime_config = _apply_runtime_overrides(session_snapshot.runtime_config, args)
    _validate_runtime_config(runtime_config)
    client = openai_client_cls(model_config)
    session_store = session_store_cls(runtime_config.session_directory)
    return (
        agent_cls(client, runtime_config, session_store),
        session_snapshot,
        loader_directory or runtime_config.session_directory.resolve(),
    )


def _build_new_model_config(args: argparse.Namespace) -> ModelConfig:
    """构建新会话使用的模型配置。"""
    base_url = (
        _normalize_optional_text(args.base_url)
        or os.getenv('OPENAI_BASE_URL', '').strip()
        or 'http://127.0.0.1:8000/v1'
    )
    base_model = ModelConfig(
        model=_required_value(args.model, 'OPENAI_MODEL', 'model'),
        base_url=base_url,
        api_key=_required_value(args.api_key, 'OPENAI_API_KEY', 'api_key'),
    )
    return _apply_model_overrides(base_model, args)


def _build_new_runtime_config(args: argparse.Namespace) -> AgentRuntimeConfig:
    """构建新会话使用的运行时配置。"""
    runtime_config = AgentRuntimeConfig(cwd=_normalize_optional_path(args.cwd) or Path('.').resolve())
    return _apply_runtime_overrides(runtime_config, args)


def _apply_model_overrides(base_model: ModelConfig, args: argparse.Namespace) -> ModelConfig:
    """把命令行模型参数覆盖到基线配置。"""
    updates: dict[str, object] = {}
    model = _normalize_optional_text(args.model)
    base_url = _normalize_optional_text(args.base_url)
    api_key = _normalize_optional_text(args.api_key)
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

    pricing = _apply_pricing_overrides(base_model.pricing, args)
    if pricing != base_model.pricing:
        updates['pricing'] = pricing

    if not updates:
        return base_model
    return replace(base_model, **updates)


def _apply_pricing_overrides(base_pricing: ModelPricing, args: argparse.Namespace) -> ModelPricing:
    """把计费相关参数覆盖到基线计费配置。"""
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


def _apply_runtime_overrides(base_runtime: AgentRuntimeConfig, args: argparse.Namespace) -> AgentRuntimeConfig:
    """把命令行运行时参数覆盖到基线配置。"""
    permissions = _apply_permission_overrides(base_runtime.permissions, args)
    budget_config = _apply_budget_overrides(base_runtime.budget_config, args)

    updates: dict[str, object] = {
        'permissions': permissions,
        'budget_config': budget_config,
    }

    cwd = _normalize_optional_path(args.cwd)
    session_directory = _normalize_optional_path(args.session_directory)
    scratchpad_root = _normalize_optional_path(args.scratchpad_root)
    additional_dirs = _normalize_optional_paths(getattr(args, 'additional_working_directories', None))

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


def _apply_permission_overrides(base_permissions: AgentPermissions, args: argparse.Namespace) -> AgentPermissions:
    """把权限开关参数覆盖到基线权限配置。"""
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


def _apply_budget_overrides(base_budget: BudgetConfig, args: argparse.Namespace) -> BudgetConfig:
    """内部方法：执行 `_apply_budget_overrides` 相关逻辑。
    Args:
        base_budget (BudgetConfig): 参数 `base_budget`。
        args (argparse.Namespace): 参数 `args`。
    Returns:
        BudgetConfig: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
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


def _validate_runtime_config(runtime_config: AgentRuntimeConfig) -> None:
    """内部方法：执行 `_validate_runtime_config` 相关逻辑。
    Args:
        runtime_config (AgentRuntimeConfig): 参数 `runtime_config`。
    Returns:
        None: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    permissions = runtime_config.permissions
    if permissions.allow_destructive_shell_commands and not permissions.allow_shell_commands:
        raise ValueError('allow_destructive_shell requires --allow-shell')


def _load_session_snapshot(
    session_id: str,
    *,
    directory: Path | None,
    session_store_cls: type[AgentSessionStore],
) -> AgentSessionSnapshot:
    """内部方法：执行 `_load_session_snapshot` 相关逻辑。
    Args:
        session_id (str): 参数 `session_id`。
        directory (Path | None): 参数 `directory`。
        session_store_cls (type[AgentSessionStore]): 参数 `session_store_cls`。
    Returns:
        AgentSessionSnapshot: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    session_store = session_store_cls(directory)
    return session_store.load(session_id)


def _normalize_optional_text(value: str | None) -> str | None:
    """内部方法：执行 `_normalize_optional_text` 相关逻辑。
    Args:
        value (str | None): 参数 `value`。
    Returns:
        str | None: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_optional_path(value: str | None) -> Path | None:
    """内部方法：执行 `_normalize_optional_path` 相关逻辑。
    Args:
        value (str | None): 参数 `value`。
    Returns:
        Path | None: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    return Path(normalized).resolve()


def _normalize_optional_paths(values: list[str] | None) -> tuple[Path, ...] | None:
    """内部方法：执行 `_normalize_optional_paths` 相关逻辑。
    Args:
        values (list[str] | None): 参数 `values`。
    Returns:
        tuple[Path, ...] | None: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    if values is None:
        return None
    normalized = [Path(item).resolve() for item in values if isinstance(item, str) and item.strip()]
    return tuple(normalized)


def _join_prompt_parts(parts: list[str]) -> str:
    """内部方法：执行 `_join_prompt_parts` 相关逻辑。
    Args:
        parts (list[str]): 参数 `parts`。
    Returns:
        str: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    return ' '.join(part.strip() for part in parts if part.strip())


def _join_optional_prompt_parts(parts: list[str]) -> str | None:
    """内部方法：执行 `_join_optional_prompt_parts` 相关逻辑。
    Args:
        parts (list[str]): 参数 `parts`。
    Returns:
        str | None: 函数返回结果。
    Raises:
        Exception: 按调用链透传的异常。
    """
    prompt = _join_prompt_parts(parts)
    return prompt or None