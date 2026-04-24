"""控制面 CLI 实现。"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable

from core_contracts.config import AgentPermissions, AgentRuntimeConfig, BudgetConfig, ModelConfig
from core_contracts.result import AgentRunResult
from core_contracts.usage import ModelPricing
from openai_client.openai_client import OpenAIClient, OpenAIClientError
from runtime.agent_runtime import LocalCodingAgent
from session.session_contracts import StoredAgentSession
from session.session_store import load_agent_session


_CHAT_EXIT_COMMANDS = {'.exit', '.quit'}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run ClawCodeAgent command surface.')
    subparsers = parser.add_subparsers(dest='command')
    subparsers.required = True

    agent_parser = subparsers.add_parser('agent', help='Run a single agent task.')
    _add_common_agent_args(agent_parser)
    agent_parser.add_argument('prompt', nargs='+', help='User prompt to run.')

    chat_parser = subparsers.add_parser('agent-chat', help='Start an interactive agent chat loop.')
    _add_common_agent_args(chat_parser)
    chat_parser.add_argument('--session-id', default='', help='Resume an existing session inside the chat loop.')
    chat_parser.add_argument('prompt', nargs='*', help='Optional initial prompt to run before the chat loop.')

    resume_parser = subparsers.add_parser('agent-resume', help='Resume a saved session with a new prompt.')
    _add_common_agent_args(resume_parser)
    resume_parser.add_argument('session_id', help='Saved session ID to resume.')
    resume_parser.add_argument('prompt', nargs='+', help='User prompt to continue with.')

    return parser


def _add_common_agent_args(parser: argparse.ArgumentParser) -> None:
    _add_model_args(parser)
    _add_runtime_args(parser)
    _add_budget_args(parser)
    _add_permission_args(parser)


def _add_model_args(parser: argparse.ArgumentParser) -> None:
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
    load_session: Callable = load_agent_session,
) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        if args.command == 'agent':
            result = _run_agent_command(args, openai_client_cls=openai_client_cls, agent_cls=agent_cls)
            print(result.final_output)
            return 0

        if args.command == 'agent-resume':
            result = _run_agent_resume_command(
                args,
                openai_client_cls=openai_client_cls,
                agent_cls=agent_cls,
                load_session=load_session,
            )
            print(result.final_output)
            return 0

        if args.command == 'agent-chat':
            return _run_agent_chat_command(
                args,
                openai_client_cls=openai_client_cls,
                agent_cls=agent_cls,
                load_session=load_session,
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
) -> AgentRunResult:
    agent, _ = _build_agent_from_args(args, openai_client_cls=openai_client_cls, agent_cls=agent_cls)
    return agent.run(_join_prompt_parts(args.prompt))


def _run_agent_resume_command(
    args: argparse.Namespace,
    *,
    openai_client_cls: type[OpenAIClient],
    agent_cls: type[LocalCodingAgent],
    load_session: Callable,
) -> AgentRunResult:
    agent, stored_session, _ = _build_resumed_agent(
        args,
        session_id=args.session_id,
        openai_client_cls=openai_client_cls,
        agent_cls=agent_cls,
        load_session=load_session,
    )
    return agent.resume(_join_prompt_parts(args.prompt), stored_session)


def _run_agent_chat_command(
    args: argparse.Namespace,
    *,
    openai_client_cls: type[OpenAIClient],
    agent_cls: type[LocalCodingAgent],
    load_session: Callable,
) -> int:
    current_session_id = _normalize_optional_text(args.session_id)
    current_session_directory = _normalize_optional_path(args.session_directory)
    pending_stored_session: StoredAgentSession | None = None

    if current_session_id:
        agent, pending_stored_session, current_session_directory = _build_resumed_agent(
            args,
            session_id=current_session_id,
            openai_client_cls=openai_client_cls,
            agent_cls=agent_cls,
            load_session=load_session,
        )
    else:
        agent, runtime_config = _build_agent_from_args(args, openai_client_cls=openai_client_cls, agent_cls=agent_cls)
        current_session_directory = current_session_directory or runtime_config.session_directory.resolve()

    initial_prompt = _join_optional_prompt_parts(args.prompt)
    if initial_prompt:
        result = _execute_chat_turn(
            agent,
            prompt=initial_prompt,
            current_session_id=current_session_id,
            current_session_directory=current_session_directory,
            stored_session=pending_stored_session,
            load_session=load_session,
        )
        pending_stored_session = None
        _render_chat_result(result, previous_session_id=current_session_id)
        current_session_id, current_session_directory = _advance_chat_state(
            result,
            current_session_id=current_session_id,
            current_session_directory=current_session_directory,
        )

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
            stored_session=pending_stored_session,
            load_session=load_session,
        )
        pending_stored_session = None
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
    stored_session: StoredAgentSession | None,
    load_session: Callable,
) -> AgentRunResult:
    if current_session_id:
        effective_session = stored_session
        if effective_session is None or effective_session.session_id != current_session_id:
            effective_session = _load_stored_session(
                current_session_id,
                directory=current_session_directory,
                load_session=load_session,
            )
        return agent.resume(prompt, effective_session)
    return agent.run(prompt)


def _render_chat_result(result: AgentRunResult, *, previous_session_id: str | None) -> None:
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
) -> tuple[LocalCodingAgent, AgentRuntimeConfig]:
    model_config = _build_new_model_config(args)
    runtime_config = _build_new_runtime_config(args)
    _validate_runtime_config(runtime_config)
    client = openai_client_cls(model_config)
    return agent_cls(client, runtime_config), runtime_config


def _build_resumed_agent(
    args: argparse.Namespace,
    *,
    session_id: str,
    openai_client_cls: type[OpenAIClient],
    agent_cls: type[LocalCodingAgent],
    load_session: Callable,
) -> tuple[LocalCodingAgent, StoredAgentSession, Path | None]:
    loader_directory = _normalize_optional_path(args.session_directory)
    stored_session = _load_stored_session(session_id, directory=loader_directory, load_session=load_session)
    model_config = _apply_model_overrides(stored_session.model_config, args)
    runtime_config = _apply_runtime_overrides(stored_session.runtime_config, args)
    _validate_runtime_config(runtime_config)
    client = openai_client_cls(model_config)
    return agent_cls(client, runtime_config), stored_session, loader_directory or runtime_config.session_directory.resolve()


def _build_new_model_config(args: argparse.Namespace) -> ModelConfig:
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
    runtime_config = AgentRuntimeConfig(cwd=_normalize_optional_path(args.cwd) or Path('.').resolve())
    return _apply_runtime_overrides(runtime_config, args)


def _apply_model_overrides(base_model: ModelConfig, args: argparse.Namespace) -> ModelConfig:
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
    permissions = runtime_config.permissions
    if permissions.allow_destructive_shell_commands and not permissions.allow_shell_commands:
        raise ValueError('allow_destructive_shell requires --allow-shell')


def _load_stored_session(
    session_id: str,
    *,
    directory: Path | None,
    load_session: Callable,
) -> StoredAgentSession:
    if directory is None:
        return load_session(session_id)
    return load_session(session_id, directory=directory)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_optional_path(value: str | None) -> Path | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    return Path(normalized).resolve()


def _normalize_optional_paths(values: list[str] | None) -> tuple[Path, ...] | None:
    if values is None:
        return None
    normalized = [Path(item).resolve() for item in values if isinstance(item, str) and item.strip()]
    return tuple(normalized)


def _join_prompt_parts(parts: list[str]) -> str:
    return ' '.join(part.strip() for part in parts if part.strip())


def _join_optional_prompt_parts(parts: list[str]) -> str | None:
    prompt = _join_prompt_parts(parts)
    return prompt or None