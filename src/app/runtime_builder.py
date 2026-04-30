"""负责 CLI 运行时依赖与静态契约装配。

本模块是 app 领域的纯内部实现，禁止外部直接导入。
RuntimeBuilder 从 argparse.Namespace 中读取所有命令行参数，
将其转换为强类型的启动规格 AgentLaunchSpec，再组装出完整的 Agent 实例。
"""

from __future__ import annotations

import argparse
import inspect
import os
from dataclasses import dataclass, replace
from pathlib import Path

from agent import AgentGateway as Agent
from core_contracts.config import BudgetConfig
from core_contracts.model import ModelConfig
from core_contracts.model import ModelPricing
from core_contracts.config import ToolPermissionPolicy
from core_contracts.config import ContextPolicy, ExecutionPolicy, SessionPaths, WorkspaceScope
from core_contracts.session import AgentSessionSnapshot
from openai_client import OpenAIClientGateway
from session.session_gateway import SessionGateway


@dataclass(frozen=True)
class AgentLaunchSpec:
    """描述一次 agent 启动所需的完整静态装配结果。

    该对象在 RuntimeBuilder 内部流转，作为 build_launch_spec → _build_agent_from_launch_spec
    之间的强类型中间态，避免散乱的参数传递。外部无需感知此类型。
    """

    model_config: ModelConfig  # ModelConfig：模型名称、base_url、api_key 及推理参数。
    workspace_scope: WorkspaceScope  # WorkspaceScope：工作区路径及多目录扩展配置。
    execution_policy: ExecutionPolicy  # ExecutionPolicy：最大轮次、命令超时等执行限制。
    context_policy: ContextPolicy  # ContextPolicy：自动压缩/截断的 token 阈值配置。
    permissions: ToolPermissionPolicy  # ToolPermissionPolicy：文件写入、shell 执行等权限开关。
    budget_config: BudgetConfig  # BudgetConfig：token 总量、成本上限等预算约束。
    session_paths: SessionPaths  # SessionPaths：会话快照目录与 scratchpad 根目录。

    @property
    def session_directory(self) -> Path:
        """返回本次启动规格使用的会话目录绝对路径。

        Args:
            无
        Returns:
            Path: 已解析的绝对路径。
        Raises:
            无。
        """
        return self.session_paths.session_directory.resolve()


@dataclass
class RuntimeBuilder:
    """封装 CLI 对 runtime 依赖的构建逻辑。

    核心工作流：
      1. build_agent_from_args / build_resumed_agent 作为公共入口；
      2. 委托 build_launch_spec 把命令行参数转换为强类型 AgentLaunchSpec；
      3. 委托 _build_agent_from_launch_spec 用 spec 实例化 client、manager 和 agent。
    """

    openai_client_cls: type[OpenAIClientGateway] = OpenAIClientGateway  # type[OpenAIClientGateway]：可注入的 OpenAI 客户端类。
    agent_cls: type[Agent] = Agent  # type[Agent]：可注入的 Agent 类。
    session_manager_cls: type[SessionGateway] = SessionGateway  # type[SessionGateway]：可注入的会话管理器类。

    def build_agent_from_args(self, args: argparse.Namespace) -> tuple[Agent, SessionPaths]:
        """根据命令行参数构造新会话代理实例。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            tuple[Agent, SessionPaths]: 新构造的 Agent 实例与本次会话路径配置。
        Raises:
            ValueError: 当必填参数（model、api_key 等）未提供时抛出。
        """
        launch_spec = self.build_launch_spec(args)
        return self._build_agent_from_launch_spec(launch_spec), launch_spec.session_paths

    def build_resumed_agent(
        self,
        args: argparse.Namespace,
        *,
        session_id: str,
    ) -> tuple[Agent, AgentSessionSnapshot, Path | None]:
        """根据持久化会话快照构造恢复态代理。

        从指定 session_id 加载快照，将其中的配置与命令行覆盖项合并，再构造 Agent。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象（覆盖项来源）。
            session_id (str): 待恢复的会话唯一标识。
        Returns:
            tuple[Agent, AgentSessionSnapshot, Path | None]:
                构造好的 Agent、会话快照对象、以及加载目录（None 表示使用默认目录）。
        Raises:
            ValueError: 当会话不存在或快照损坏时抛出。
        """
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
        """把 CLI 参数与可选快照基线装配成统一启动规格。

        无快照时从命令行参数全量构建；有快照时以快照为基线，
        再把命令行中的覆盖项叠加进去，最后做跨字段约束校验。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
            session_snapshot (AgentSessionSnapshot | None): 恢复模式时传入的会话快照。
        Returns:
            AgentLaunchSpec: 已校验的完整启动规格。
        Raises:
            ValueError: 当静态契约约束不满足时抛出（如 destructive_shell 未配合 allow_shell）。
        """
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
        """按会话 ID 从持久化存储中加载快照。

        Args:
            session_id (str): 待加载的会话唯一标识。
            directory (Path | None): 快照目录；为 None 时使用 SessionGateway 默认路径。
        Returns:
            AgentSessionSnapshot: 已反序列化的会话快照对象。
        Raises:
            ValueError: 当会话不存在或反序列化失败时抛出。
        """
        session_manager = self.session_manager_cls(directory)
        return session_manager.load_session(session_id)

    # ── 私有辅助：按深度优先调用顺序排列 ────────────────────────────────────

    def _build_agent_from_launch_spec(self, launch_spec: AgentLaunchSpec) -> Agent:
        """根据统一启动规格实例化 client、session_manager 与 agent。

        Args:
            launch_spec (AgentLaunchSpec): 已校验的完整启动规格。
        Returns:
            Agent: 构造好并可立即使用的代理实例。
        Raises:
            无（依赖注入的类若构造失败会向上透传）。
        """
        client = self.openai_client_cls(launch_spec.model_config)
        session_manager = self.session_manager_cls(launch_spec.session_directory)
        agent_ctor_params = inspect.signature(self.agent_cls).parameters
        candidate_kwargs = {
            'client': client,
            'model_config': launch_spec.model_config,
            'workspace_scope': launch_spec.workspace_scope,
            'execution_policy': launch_spec.execution_policy,
            'context_policy': launch_spec.context_policy,
            'permissions': launch_spec.permissions,
            'session_paths': launch_spec.session_paths,
            'session_store': session_manager,
            'session_manager': session_manager,
            'session_gateway': session_manager,
            'budget_config': launch_spec.budget_config,
        }
        ctor_kwargs = {name: value for name, value in candidate_kwargs.items() if name in agent_ctor_params}
        agent = self.agent_cls(**ctor_kwargs)
        if 'model_config' not in agent_ctor_params:
            try:
                setattr(agent, 'model_config', launch_spec.model_config)
            except AttributeError:
                pass
        return agent

    def _normalize_optional_path(self, value: str | None) -> Path | None:
        """清洗可选路径文本并解析为绝对路径。

        Args:
            value (str | None): 原始路径字符串，可能为 None 或纯空白。
        Returns:
            Path | None: 已解析的绝对路径；输入为空时返回 None。
        Raises:
            无。
        """
        normalized = self._normalize_optional_text(value)
        if normalized is None:
            return None
        return Path(normalized).resolve()

    def _normalize_optional_text(self, value: str | None) -> str | None:
        """清洗可选文本，把空白值折叠为 None。

        Args:
            value (str | None): 原始字符串，可能为 None 或纯空白。
        Returns:
            str | None: 去除首尾空白后的字符串；空白或 None 输入返回 None。
        Raises:
            无。
        """
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _build_new_model_config(self, args: argparse.Namespace) -> ModelConfig:
        """构建新会话使用的模型配置。

        先以 base_url / model / api_key 构建基线，再叠加覆盖项。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ModelConfig: 包含全部模型参数的配置对象。
        Raises:
            ValueError: 当 model 或 api_key 未在命令行或环境变量中提供时抛出。
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
            cli_value (str | None): 命令行中提供的原始值（可能为 None）。
            env_key (str): 对应的环境变量名（fallback 来源）。
            field_name (str): 字段逻辑名称，用于错误消息中的提示。
        Returns:
            str: 非空的必填文本值。
        Raises:
            ValueError: 当命令行和环境变量均未提供时抛出。
        """
        normalized = self._normalize_optional_text(cli_value)
        if normalized:
            return normalized
        env_value = os.getenv(env_key, '').strip()
        if env_value:
            return env_value
        raise ValueError(f'Missing required {field_name}. Use --{field_name.replace("_", "-")} or {env_key}.')

    def _apply_model_overrides(self, base_model: ModelConfig, args: argparse.Namespace) -> ModelConfig:
        """把命令行中的模型覆盖项合并到基线模型配置。

        仅当对应参数非 None 时才生成更新，避免覆盖快照基线中已有的合法值。

        Args:
            base_model (ModelConfig): 基线模型配置（新建或来自快照）。
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ModelConfig: 已合并覆盖项的模型配置（若无覆盖则返回原对象）。
        Raises:
            无。
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
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ModelPricing: 已合并计费覆盖项的配置（若无覆盖则返回原对象）。
        Raises:
            无。
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

    def _build_new_workspace_scope(self, args: argparse.Namespace) -> WorkspaceScope:
        """构建新会话使用的工作区范围配置。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            WorkspaceScope: 以当前目录为基础并应用覆盖项的工作区配置。
        Raises:
            无。
        """
        return self._apply_workspace_scope_overrides(
            WorkspaceScope(cwd=self._normalize_optional_path(args.cwd) or Path('.').resolve()),
            args,
        )

    def _apply_workspace_scope_overrides(self, base_scope: WorkspaceScope, args: argparse.Namespace) -> WorkspaceScope:
        """把命令行中的工作区覆盖项合并到基线工作区范围配置。

        Args:
            base_scope (WorkspaceScope): 基线工作区配置（新建或来自快照）。
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            WorkspaceScope: 已合并覆盖项的工作区配置（若无覆盖则返回原对象）。
        Raises:
            无。
        """
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

    def _normalize_optional_paths(self, values: list[str] | None) -> tuple[Path, ...] | None:
        """把可选路径字符串列表解析为绝对路径元组。

        Args:
            values (list[str] | None): 原始路径字符串列表，可能为 None。
        Returns:
            tuple[Path, ...] | None: 已解析的绝对路径元组；输入为 None 时返回 None。
        Raises:
            无。
        """
        if values is None:
            return None
        normalized = [Path(item).resolve() for item in values if isinstance(item, str) and item.strip()]
        return tuple(normalized)

    def _build_new_execution_policy(self, args: argparse.Namespace) -> ExecutionPolicy:
        """构建新会话使用的执行限制配置。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ExecutionPolicy: 已应用覆盖项的执行配置。
        Raises:
            无。
        """
        return self._apply_execution_overrides(ExecutionPolicy(), args)

    def _apply_execution_overrides(self, base_policy: ExecutionPolicy, args: argparse.Namespace) -> ExecutionPolicy:
        """把命令行中的执行覆盖项合并到基线执行限制配置。

        Args:
            base_policy (ExecutionPolicy): 基线执行配置（新建或来自快照）。
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ExecutionPolicy: 已合并覆盖项的执行配置（若无覆盖则返回原对象）。
        Raises:
            无。
        """
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

    def _build_new_context_policy(self, args: argparse.Namespace) -> ContextPolicy:
        """构建新会话使用的上下文治理策略。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ContextPolicy: 已应用覆盖项的上下文策略。
        Raises:
            无。
        """
        return self._apply_context_policy_overrides(ContextPolicy(), args)

    def _apply_context_policy_overrides(self, base_policy: ContextPolicy, args: argparse.Namespace) -> ContextPolicy:
        """把命令行中的上下文覆盖项合并到基线上下文治理策略。

        Args:
            base_policy (ContextPolicy): 基线上下文策略（新建或来自快照）。
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ContextPolicy: 已合并覆盖项的上下文策略（若无覆盖则返回原对象）。
        Raises:
            无。
        """
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

    def _build_new_permissions(self, args: argparse.Namespace) -> ToolPermissionPolicy:
        """构建新会话使用的权限策略。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ToolPermissionPolicy: 已应用覆盖项的权限配置。
        Raises:
            无。
        """
        return self._apply_permission_overrides(ToolPermissionPolicy(), args)

    def _apply_permission_overrides(self, base_permissions: ToolPermissionPolicy, args: argparse.Namespace) -> ToolPermissionPolicy:
        """把权限开关参数覆盖到基线权限配置。

        Args:
            base_permissions (ToolPermissionPolicy): 基线权限配置（新建或来自快照）。
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            ToolPermissionPolicy: 已合并覆盖项的权限配置（若无覆盖则返回原对象）。
        Raises:
            无。
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

    def _build_new_budget_config(self, args: argparse.Namespace) -> BudgetConfig:
        """构建新会话使用的预算配置。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            BudgetConfig: 已应用覆盖项的预算配置。
        Raises:
            无。
        """
        return self._apply_budget_overrides(BudgetConfig(), args)

    def _apply_budget_overrides(self, base_budget: BudgetConfig, args: argparse.Namespace) -> BudgetConfig:
        """把预算约束参数覆盖到基线预算配置。

        Args:
            base_budget (BudgetConfig): 基线预算配置（新建或来自快照）。
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            BudgetConfig: 已合并覆盖项的预算配置（若无覆盖则返回原对象）。
        Raises:
            无。
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

    def _build_new_session_paths(self, args: argparse.Namespace) -> SessionPaths:
        """构建新会话使用的会话路径配置。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            SessionPaths: 已应用覆盖项的会话路径配置。
        Raises:
            无。
        """
        return self._apply_session_path_overrides(SessionPaths(), args)

    def _apply_session_path_overrides(self, base_paths: SessionPaths, args: argparse.Namespace) -> SessionPaths:
        """把命令行中的会话路径覆盖项合并到基线会话路径配置。

        Args:
            base_paths (SessionPaths): 基线会话路径配置（新建或来自快照）。
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            SessionPaths: 已合并覆盖项的会话路径配置（若无覆盖则返回原对象）。
        Raises:
            无。
        """
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

    @staticmethod
    def _validate_static_contracts(permissions: ToolPermissionPolicy) -> None:
        """校验静态契约的跨字段约束。

        当前规则：allow_destructive_shell 要求 allow_shell 同时开启。

        Args:
            permissions (ToolPermissionPolicy): 待校验的权限配置对象。
        Returns:
            None
        Raises:
            ValueError: 当约束不满足时抛出，并给出可读的修复建议。
        """
        if permissions.allow_destructive_shell_commands and not permissions.allow_shell_commands:
            raise ValueError('allow_destructive_shell requires --allow-shell')

    @staticmethod
    def resolve_show_progress(args: argparse.Namespace) -> bool:
        """解析交互式 progress 输出开关。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            bool: True 表示开启进度输出；未传参时默认为 True。
        Raises:
            无。
        """
        if args.show_progress is None:
            return True
        return bool(args.show_progress)




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
