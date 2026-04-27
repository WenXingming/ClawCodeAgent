"""配置对象相关契约。

定义Agent运行期间所需的各类配置对象，包括预算限制、权限控制、模型配置、输出schema等。
所有配置对象均为frozen dataclass，提供to_dict()序列化与from_dict()反序列化接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._coerce import (
    _as_bool,
    _as_dict,
    _as_float,
    _as_int,
    _as_optional_float,
    _as_optional_int,
    _as_str,
    _first_present,
    _path_or_default,
)
from .protocol import JSONDict
from .model_pricing import ModelPricing


@dataclass(frozen=True)
class BudgetConfig:
    """运行期预算限制，用于保证安全和可预测性。
    
    包含多个可选的预算限制维度，超过任一限制时主循环会中止执行。
    """

    max_total_tokens: int | None = None  # 会话总token限制
    max_input_tokens: int | None = None  # 单次输入token限制
    max_output_tokens: int | None = None  # 单次输出token限制
    max_reasoning_tokens: int | None = None  # 推理token总限制
    max_total_cost_usd: float | None = None  # 会话成本上限（美元）
    max_tool_calls: int | None = None  # 工具调用总数限制
    max_delegated_tasks: int | None = None  # 委派任务数限制
    max_model_calls: int | None = None  # 模型调用次数限制
    max_session_turns: int | None = None  # 会话turn数限制

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        
        Returns:
            JSONDict: 包含所有预算限制配置的字典
        """
        return {
            'max_total_tokens': self.max_total_tokens,
            'max_input_tokens': self.max_input_tokens,
            'max_output_tokens': self.max_output_tokens,
            'max_reasoning_tokens': self.max_reasoning_tokens,
            'max_total_cost_usd': self.max_total_cost_usd,
            'max_tool_calls': self.max_tool_calls,
            'max_delegated_tasks': self.max_delegated_tasks,
            'max_model_calls': self.max_model_calls,
            'max_session_turns': self.max_session_turns,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'BudgetConfig':
        """反序列化为 BudgetConfig 对象。
        
        支持snake_case和camelCase两种字段命名。
        
        Args:
            payload (JSONDict | None): 待反序列化的配置字典
            
        Returns:
            BudgetConfig: 反序列化后的预算配置对象
        """
        data = _as_dict(payload)
        return cls(
            max_total_tokens=_as_optional_int(
                _first_present(data, 'max_total_tokens', 'maxTotalTokens')
            ),
            max_input_tokens=_as_optional_int(
                _first_present(data, 'max_input_tokens', 'maxInputTokens')
            ),
            max_output_tokens=_as_optional_int(
                _first_present(data, 'max_output_tokens', 'maxOutputTokens')
            ),
            max_reasoning_tokens=_as_optional_int(
                _first_present(data, 'max_reasoning_tokens', 'maxReasoningTokens')
            ),
            max_total_cost_usd=_as_optional_float(
                _first_present(data, 'max_total_cost_usd', 'maxTotalCostUsd')
            ),
            max_tool_calls=_as_optional_int(
                _first_present(data, 'max_tool_calls', 'maxToolCalls')
            ),
            max_delegated_tasks=_as_optional_int(
                _first_present(data, 'max_delegated_tasks', 'maxDelegatedTasks')
            ),
            max_model_calls=_as_optional_int(
                _first_present(data, 'max_model_calls', 'maxModelCalls')
            ),
            max_session_turns=_as_optional_int(
                _first_present(data, 'max_session_turns', 'maxSessionTurns')
            ),
        )


@dataclass(frozen=True)
class OutputSchemaConfig:
    """可选的结构化输出 schema 配置。
    
    若模型支持结构化输出模式，此配置指定期望的JSON schema和是否严格验证。
    """

    name: str # schema 名称，用于区分不同的输出格式规范
    schema: JSONDict # JSON Schema 定义，描述预期的输出结构和类型
    strict: bool = False # 是否严格验证输出，默认为 False，允许额外字段存在

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        
        Returns:
            JSONDict: 包含name/schema/strict的字典
        """
        return {
            'name': self.name,
            'schema': dict(self.schema),
            'strict': self.strict,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'OutputSchemaConfig | None':
        """反序列化为 OutputSchemaConfig 对象。
        
        若输入不包含必要字段则返回None。
        
        Args:
            payload (JSONDict | None): 待反序列化的配置字典
            
        Returns:
            OutputSchemaConfig | None: 反序列化后的输出schema配置，或None（若输入无效）
        """
        data = _as_dict(payload)
        name = _as_str(data.get('name'), '').strip()
        schema = data.get('schema')
        if not name or not isinstance(schema, dict):
            return None
        return cls(
            name=name,
            schema=dict(schema),
            strict=_as_bool(data.get('strict'), False),
        )


@dataclass(frozen=True)
class ModelConfig:
    """OpenAI-compatible 客户端使用的模型后端配置。
    
    指定模型名称、API端点、认证信息、运行参数、以及计费配置。
    """

    model: str  # 模型标识符，如 'gpt-4', 'claude-3-sonnet' 等
    base_url: str = 'http://127.0.0.1:8000/v1'  # API端点基础URL，默认本地dev服务器
    api_key: str = 'local-token'  # API认证密钥，默认本地token
    temperature: float = 0.0  # 生成温度参数（0.0=确定性，1.0=最大随机性）
    timeout_seconds: float = 120.0  # API请求超时时间（秒）
    pricing: ModelPricing = field(default_factory=ModelPricing)  # 模型计费配置

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        
        Returns:
            JSONDict: 包含所有模型配置的字典
        """
        return {
            'model': self.model,
            'base_url': self.base_url,
            'api_key': self.api_key,
            'temperature': self.temperature,
            'timeout_seconds': self.timeout_seconds,
            'pricing': self.pricing.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ModelConfig':
        """反序列化为 ModelConfig 对象。
        
        支持snake_case和camelCase两种字段命名。
        
        Args:
            payload (JSONDict | None): 待反序列化的配置字典
            
        Returns:
            ModelConfig: 反序列化后的模型配置对象
        """
        data = _as_dict(payload)
        model = _as_str(data.get('model'), '').strip() or 'unknown-model'
        return cls(
            model=model,
            base_url=_as_str(
                _first_present(data, 'base_url', 'baseUrl', default='http://127.0.0.1:8000/v1'),
                'http://127.0.0.1:8000/v1',
            ),
            api_key=_as_str(_first_present(data, 'api_key', 'apiKey', default='local-token'), 'local-token'),
            temperature=_as_float(data.get('temperature'), 0.0),
            timeout_seconds=_as_float(
                _first_present(data, 'timeout_seconds', 'timeoutSeconds', default=120.0),
                120.0,
            ),
            pricing=ModelPricing.from_dict(data.get('pricing')),
        )


@dataclass(frozen=True)
class AgentPermissions:
    """运行时和工具执行使用的权限开关。
    
    控制Agent是否允许执行特定类别的操作（文件写入、shell命令、销毁性操作等）。
    """

    allow_file_write: bool = False  # 是否允许Agent写文件
    allow_shell_commands: bool = False  # 是否允许执行shell命令
    allow_destructive_shell_commands: bool = False  # 是否允许执行销毁性shell操作

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        
        Returns:
            JSONDict: 包含所有权限开关的字典
        """
        return {
            'allow_file_write': self.allow_file_write,
            'allow_shell_commands': self.allow_shell_commands,
            'allow_destructive_shell_commands': self.allow_destructive_shell_commands,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'AgentPermissions':
        """反序列化为 AgentPermissions 对象。
        
        支持snake_case和camelCase两种字段命名。
        
        Args:
            payload (JSONDict | None): 待反序列化的权限配置字典
            
        Returns:
            AgentPermissions: 反序列化后的权限对象
        """
        data = _as_dict(payload)
        return cls(
            allow_file_write=_as_bool(
                _first_present(data, 'allow_file_write', 'allowFileWrite'),
                False,
            ),
            allow_shell_commands=_as_bool(
                _first_present(data, 'allow_shell_commands', 'allowShellCommands'),
                False,
            ),
            allow_destructive_shell_commands=_as_bool(
                _first_present(
                    data,
                    'allow_destructive_shell_commands',
                    'allowDestructiveShellCommands',
                ),
                False,
            ),
        )


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """运行配置：执行选项与工作目录路径。
    
    综合配置类，包含运行行为参数、权限控制、预算限制、会话存储路径等。
    """

    cwd: Path  # 工作目录绝对路径
    max_turns: int = 12  # 单个会话的最大turn数
    command_timeout_seconds: float = 30.0  # shell命令执行超时时间（秒）
    max_output_chars: int = 12000  # 单次命令输出的最大字符数
    stream_model_responses: bool = False  # 是否流式输出模型响应
    auto_snip_threshold_tokens: int | None = None  # 自动snip的token阈值；None表示禁用
    auto_compact_threshold_tokens: int | None = None  # 自动compact的token阈值；None表示禁用
    compact_preserve_messages: int = 4  # compact时保留的最近消息数
    permissions: AgentPermissions = field(default_factory=AgentPermissions)  # 权限配置
    additional_working_directories: tuple[Path, ...] = ()  # 额外的工作目录（用于文件访问限制）
    disable_claude_md_discovery: bool = False  # 是否禁用claude.md自动发现
    budget_config: BudgetConfig = field(default_factory=BudgetConfig)  # 预算限制配置
    output_schema: OutputSchemaConfig | None = None  # 结构化输出schema配置
    session_directory: Path = field(default_factory=lambda: (Path('.port_sessions') / 'agent').resolve())  # 会话存储目录
    scratchpad_root: Path = field(default_factory=lambda: (Path('.port_sessions') / 'scratchpad').resolve())  # 临时工作目录

    def to_dict(self) -> JSONDict:
        """序列化为字典。
        
        所有Path对象转换为字符串；嵌套配置对象调用其to_dict()方法。
        
        Returns:
            JSONDict: 包含所有运行配置的字典
        """
        return {
            'cwd': str(self.cwd),
            'max_turns': self.max_turns,
            'command_timeout_seconds': self.command_timeout_seconds,
            'max_output_chars': self.max_output_chars,
            'stream_model_responses': self.stream_model_responses,
            'auto_snip_threshold_tokens': self.auto_snip_threshold_tokens,
            'auto_compact_threshold_tokens': self.auto_compact_threshold_tokens,
            'compact_preserve_messages': self.compact_preserve_messages,
            'permissions': self.permissions.to_dict(),
            'additional_working_directories': [str(path) for path in self.additional_working_directories],
            'disable_claude_md_discovery': self.disable_claude_md_discovery,
            'budget_config': self.budget_config.to_dict(),
            'output_schema': self.output_schema.to_dict() if self.output_schema else None,
            'session_directory': str(self.session_directory),
            'scratchpad_root': str(self.scratchpad_root),
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'AgentRuntimeConfig':
        """反序列化为 AgentRuntimeConfig 对象。
        
        支持snake_case和camelCase两种字段命名；将字符串路径转换为Path对象。
        
        Args:
            payload (JSONDict | None): 待反序列化的运行配置字典
            
        Returns:
            AgentRuntimeConfig: 反序列化后的运行时配置对象
        """
        data = _as_dict(payload)
        default_session_dir = (Path('.port_sessions') / 'agent').resolve()
        default_scratchpad_root = (Path('.port_sessions') / 'scratchpad').resolve()
        additional_dirs_raw = data.get(
            'additional_working_directories', data.get('additionalWorkingDirectories', [])
        )
        if not isinstance(additional_dirs_raw, list):
            additional_dirs_raw = []

        return cls(
            cwd=_path_or_default(data.get('cwd'), Path('.').resolve()),
            max_turns=_as_int(_first_present(data, 'max_turns', 'maxTurns', default=12), 12),
            command_timeout_seconds=_as_float(
                _first_present(
                    data,
                    'command_timeout_seconds',
                    'commandTimeoutSeconds',
                    default=30.0,
                ),
                30.0,
            ),
            max_output_chars=_as_int(
                _first_present(data, 'max_output_chars', 'maxOutputChars', default=12000),
                12000,
            ),
            stream_model_responses=_as_bool(
                _first_present(data, 'stream_model_responses', 'streamModelResponses'),
                False,
            ),
            auto_snip_threshold_tokens=_as_optional_int(
                _first_present(
                    data,
                    'auto_snip_threshold_tokens',
                    'autoSnipThresholdTokens',
                )
            ),
            auto_compact_threshold_tokens=_as_optional_int(
                _first_present(
                    data,
                    'auto_compact_threshold_tokens',
                    'autoCompactThresholdTokens',
                )
            ),
            compact_preserve_messages=_as_int(
                _first_present(
                    data,
                    'compact_preserve_messages',
                    'compactPreserveMessages',
                    default=4,
                ),
                4,
            ),
            permissions=AgentPermissions.from_dict(data.get('permissions')),
            additional_working_directories=tuple(
                Path(str(item)).resolve()
                for item in additional_dirs_raw
                if isinstance(item, str) and item.strip()
            ),
            disable_claude_md_discovery=_as_bool(
                _first_present(
                    data,
                    'disable_claude_md_discovery',
                    'disableClaudeMdDiscovery',
                ),
                False,
            ),
            budget_config=BudgetConfig.from_dict(
                _first_present(data, 'budget_config', 'budgetConfig', default={})
            ),
            output_schema=OutputSchemaConfig.from_dict(
                _first_present(data, 'output_schema', 'outputSchema', default=None)
            ),
            session_directory=_path_or_default(data.get('session_directory'), default_session_dir),
            scratchpad_root=_path_or_default(data.get('scratchpad_root'), default_scratchpad_root),
        )