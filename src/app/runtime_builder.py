"""负责 CLI 运行时依赖与静态契约装配。"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, replace
from pathlib import Path

from agent import Agent
from core_contracts.budget import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.model_pricing import ModelPricing
from core_contracts.permissions import ToolPermissionPolicy
from core_contracts.runtime_policy import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from openai_client.openai_client import OpenAIClient
from session.session_snapshot import AgentSessionSnapshot
from session.session_store import AgentSessionStore


@dataclass(frozen=True)
class AgentLaunchSpec:
    """描述一次 agent 启动所需的静态装配结果。"""

    model_config: ModelConfig
    workspace_scope: WorkspaceScope
    execution_policy: ExecutionPolicy
    context_policy: ContextPolicy
    permissions: ToolPermissionPolicy
    budget_config: BudgetConfig
    session_paths: SessionPaths

    @property
    def session_directory(self) -> Path:
        """返回本次启动规格使用的会话目录绝对路径。"""
        return self.session_paths.session_directory.resolve()


@dataclass
class RuntimeBuilder:
    """封装 CLI 对 runtime 依赖的构建逻辑。"""

    openai_client_cls: type[OpenAIClient] = OpenAIClient
    agent_cls: type[Agent] = Agent
    session_store_cls: type[AgentSessionStore] = AgentSessionStore

    def build_agent_from_args(self, args: argparse.Namespace) -> tuple[Agent, SessionPaths]:
        """根据命令行参数构造新会话代理实例。"""
        launch_spec = self.build_launch_spec(args)
        return self._build_agent_from_launch_spec(launch_spec), launch_spec.session_paths

    def build_resumed_agent(
        self,
        args: argparse.Namespace,
        *,
        session_id: str,
    ) -> tuple[Agent, AgentSessionSnapshot, Path | None]:
        """根据持久化会话快照构造恢复态代理。"""
        loader_directory = self._normalize_optional_path(args.session_directory)
        session_snapshot = self.load_session_snapshot(
            session_id,
            directory=loader_directory,
        )
        launch_spec = self.build_launch_spec(args, session_snapshot=session_snapshot)
        return (
            self._build_agent_from_launch_spec(launch_spec),
            session_snapshot,
            loader_directory or launch_spec.session_directory,
        )

    def build_launch_spec(
        self,
        args: argparse.Namespace,
        *,
        session_snapshot: AgentSessionSnapshot | None = None,
    ) -> AgentLaunchSpec:
        """把 CLI 参数与可选快照基线装配成统一启动规格。"""
        if session_snapshot is None:
            launch_spec = AgentLaunchSpec(
                model_config=self._build_new_model_config(args),
                workspace_scope=self._build_new_workspace_scope(args),
                execution_policy=self._build_new_execution_policy(args),
                context_policy=self._build_new_context_policy(args),
                permissions=self._build_new_permissions(args),
                budget_config=self._build_new_budget_config(args),
                session_paths=self._build_new_session_paths(args),
            )
        else:
            launch_spec = AgentLaunchSpec(
                model_config=self._apply_model_overrides(session_snapshot.model_config, args),
                workspace_scope=self._apply_workspace_scope_overrides(session_snapshot.workspace_scope, args),
                execution_policy=self._apply_execution_overrides(session_snapshot.execution_policy, args),
                context_policy=self._apply_context_policy_overrides(session_snapshot.context_policy, args),
                permissions=self._apply_permission_overrides(session_snapshot.permissions, args),
                budget_config=self._apply_budget_overrides(session_snapshot.budget_config, args),
                session_paths=self._apply_session_path_overrides(session_snapshot.session_paths, args),
            )

        self._validate_static_contracts(launch_spec.permissions)
        return launch_spec

    def load_session_snapshot(
        self,
        session_id: str,
        *,
        directory: Path | None,
    ) -> AgentSessionSnapshot:
        """按会话 ID 从持久化存储中加载快照。"""
        session_store = self.session_store_cls(directory)
        return session_store.load(session_id)

    def _build_agent_from_launch_spec(self, launch_spec: AgentLaunchSpec) -> Agent:
        """根据统一启动规格实例化 client、store 与 agent。"""
        client = self.openai_client_cls(launch_spec.model_config)
        session_store = self.session_store_cls(launch_spec.session_directory)
        return self.agent_cls(
            client,
            launch_spec.workspace_scope,
            launch_spec.execution_policy,
            launch_spec.context_policy,
            launch_spec.permissions,
            launch_spec.budget_config,
            launch_spec.session_paths,
            session_store,
        )

    def _build_new_model_config(self, args: argparse.Namespace) -> ModelConfig:
        """构建新会话使用的模型配置。"""
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
        """从命令行参数或环境变量中读取必填文本值。"""
        normalized = self._normalize_optional_text(cli_value)
        if normalized:
            return normalized

        env_value = os.getenv(env_key, '').strip()
        if env_value:
            return env_value
        raise ValueError(f'Missing required {field_name}. Use --{field_name.replace("_", "-")} or {env_key}.')

    def _normalize_optional_text(self, value: str | None) -> str | None:
        """清洗可选文本，把空白值折叠为 None。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _apply_model_overrides(self, base_model: ModelConfig, args: argparse.Namespace) -> ModelConfig:
        """把命令行中的模型覆盖项合并到基线模型配置。"""
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
        """把计费相关命令行参数覆盖到基线计费配置。"""
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

    def _build_new_workspace_scope(self, args: argparse.Namespace) -> WorkspaceScope:
        """构建新会话使用的工作区范围配置。"""
        return self._apply_workspace_scope_overrides(
            WorkspaceScope(cwd=self._normalize_optional_path(args.cwd) or Path('.').resolve()),
            args,
        )

    def _build_new_execution_policy(self, args: argparse.Namespace) -> ExecutionPolicy:
        """构建新会话使用的执行限制配置。"""
        return self._apply_execution_overrides(ExecutionPolicy(), args)

    def _build_new_context_policy(self, args: argparse.Namespace) -> ContextPolicy:
        """构建新会话使用的上下文治理策略。"""
        return self._apply_context_policy_overrides(ContextPolicy(), args)

    def _build_new_permissions(self, args: argparse.Namespace) -> ToolPermissionPolicy:
        """构建新会话使用的权限策略。"""
        return self._apply_permission_overrides(ToolPermissionPolicy(), args)

    def _build_new_budget_config(self, args: argparse.Namespace) -> BudgetConfig:
        """构建新会话使用的预算配置。"""
        return self._apply_budget_overrides(BudgetConfig(), args)

    def _build_new_session_paths(self, args: argparse.Namespace) -> SessionPaths:
        """构建新会话使用的会话路径配置。"""
        return self._apply_session_path_overrides(SessionPaths(), args)

    def _normalize_optional_path(self, value: str | None) -> Path | None:
        """清洗可选路径文本并解析为绝对路径。"""
        normalized = self._normalize_optional_text(value)
        if normalized is None:
            return None
        return Path(normalized).resolve()

    def _apply_workspace_scope_overrides(self, base_scope: WorkspaceScope, args: argparse.Namespace) -> WorkspaceScope:
        """把命令行中的工作区覆盖项合并到基线工作区范围配置。"""
        updates: dict[str, object] = {}
        cwd = self._normalize_optional_path(args.cwd)
        additional_dirs = self._normalize_optional_paths(getattr(args, 'additional_working_directories', None))
        if cwd is not None:
            updates['cwd'] = cwd
        if additional_dirs is not None:
            updates['additional_working_directories'] = additional_dirs
        if args.disable_claude_md_discovery is not None:
            updates['disable_claude_md_discovery'] = args.disable_claude_md_discovery
        if not updates:
            return base_scope
        return replace(base_scope, **updates)

    def _apply_execution_overrides(self, base_policy: ExecutionPolicy, args: argparse.Namespace) -> ExecutionPolicy:
        """把命令行中的执行覆盖项合并到基线执行限制配置。"""
        updates: dict[str, object] = {}
        if args.max_turns is not None:
            updates['max_turns'] = args.max_turns
        if args.command_timeout_seconds is not None:
            updates['command_timeout_seconds'] = args.command_timeout_seconds
        if args.max_output_chars is not None:
            updates['max_output_chars'] = args.max_output_chars
        if args.stream_model_responses is not None:
            updates['stream_model_responses'] = args.stream_model_responses
        if not updates:
            return base_policy
        return replace(base_policy, **updates)

    def _apply_context_policy_overrides(self, base_policy: ContextPolicy, args: argparse.Namespace) -> ContextPolicy:
        """把命令行中的上下文覆盖项合并到基线上下文治理策略。"""
        updates: dict[str, object] = {}
        if args.auto_snip_threshold_tokens is not None:
            updates['auto_snip_threshold_tokens'] = args.auto_snip_threshold_tokens
        if args.auto_compact_threshold_tokens is not None:
            updates['auto_compact_threshold_tokens'] = args.auto_compact_threshold_tokens
        if args.compact_preserve_messages is not None:
            updates['compact_preserve_messages'] = args.compact_preserve_messages
        if not updates:
            return base_policy
        return replace(base_policy, **updates)

    def _apply_session_path_overrides(self, base_paths: SessionPaths, args: argparse.Namespace) -> SessionPaths:
        """把命令行中的会话路径覆盖项合并到基线会话路径配置。"""
        updates: dict[str, object] = {}
        session_directory = self._normalize_optional_path(args.session_directory)
        scratchpad_root = self._normalize_optional_path(args.scratchpad_root)
        if session_directory is not None:
            updates['session_directory'] = session_directory
        if scratchpad_root is not None:
            updates['scratchpad_root'] = scratchpad_root
        if not updates:
            return base_paths
        return replace(base_paths, **updates)

    def _apply_permission_overrides(self, base_permissions: ToolPermissionPolicy, args: argparse.Namespace) -> ToolPermissionPolicy:
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

    def _apply_budget_overrides(self, base_budget: BudgetConfig, args: argparse.Namespace) -> BudgetConfig:
        """把预算约束参数覆盖到基线预算配置。"""
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
        """把可选路径字符串列表解析为绝对路径元组。"""
        if values is None:
            return None
        normalized = [Path(item).resolve() for item in values if isinstance(item, str) and item.strip()]
        return tuple(normalized)

    @staticmethod
    def resolve_show_progress(args: argparse.Namespace) -> bool:
        """解析交互式 progress 输出开关。"""
        if args.show_progress is None:
            return True
        return bool(args.show_progress)

    @staticmethod
    def _validate_static_contracts(permissions: ToolPermissionPolicy) -> None:
        """校验静态契约的跨字段约束。"""
        if permissions.allow_destructive_shell_commands and not permissions.allow_shell_commands:
            raise ValueError('allow_destructive_shell requires --allow-shell')
