"""core_contracts 领域唯一公开入口。

本文件集中重导出 core_contracts 包内全部公开类型，
外部模块应仅通过 `from core_contracts import ...` 引用本包内容。
"""

from core_contracts.primitives import JSONDict, TokenUsage
from core_contracts.errors import (
    GatewayError,
    GatewayNotFoundError,
    GatewayPermissionError,
    GatewayRuntimeError,
    GatewayTransportError,
    GatewayValidationError,
    ModelConnectionError,
    ModelGatewayError,
    ModelResponseError,
    ModelTimeoutError,
)
from core_contracts.model import ModelClient, ModelConfig, ModelPricing, StructuredOutputSpec
from core_contracts.messaging import OneTurnResponse, StreamEvent, ToolCall, ToolExecutionResult
from core_contracts.config import (
    BudgetConfig,
    ContextPolicy,
    ExecutionPolicy,
    SessionPaths,
    ToolPermissionPolicy,
    WorkspaceScope,
)
from core_contracts.tools import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolHandler,
    ToolStreamHandler,
    ToolStreamUpdate,
    build_execution_context,
)
from core_contracts.session import AgentSessionSnapshot, AgentSessionState
from core_contracts.context import (
    BudgetProjection,
    CompactionResult,
    ContextRunState,
    PreModelBudgetGuard,
    PreModelContextOutcome,
    ReactiveCompactOutcome,
    SessionMessageView,
    SnipResult,
)
from core_contracts.outcomes import AgentRunResult, QueryServiceConfig, QueryTurnResult
from core_contracts.interaction import (
    EnvironmentLoadSummary,
    SessionSummary,
    SlashAutocompleteEntry,
    SlashCommandContext,
    SlashCommandResult,
)
from core_contracts.planning import (
    PlanStep,
    PlanStepStatus,
    TaskRecord,
    TaskStatus,
    WorkflowAction,
    WorkflowLoadError,
    WorkflowManifest,
    WorkflowRunRecord,
    WorkflowRunStatus,
    WorkflowStepResult,
    WorkflowStepSpec,
)

__all__ = [
    # ── primitives ──
    'JSONDict',
    'TokenUsage',
    # ── errors ──
    'GatewayError',
    'GatewayNotFoundError',
    'GatewayPermissionError',
    'GatewayRuntimeError',
    'GatewayTransportError',
    'GatewayValidationError',
    'ModelConnectionError',
    'ModelGatewayError',
    'ModelResponseError',
    'ModelTimeoutError',
    # ── model ──
    'ModelClient',
    'ModelConfig',
    'ModelPricing',
    'StructuredOutputSpec',
    # ── messaging ──
    'OneTurnResponse',
    'StreamEvent',
    'ToolCall',
    'ToolExecutionResult',
    # ── config ──
    'BudgetConfig',
    'ContextPolicy',
    'ExecutionPolicy',
    'SessionPaths',
    'ToolPermissionPolicy',
    'WorkspaceScope',
    # ── tools ──
    'ToolDescriptor',
    'ToolExecutionContext',
    'ToolHandler',
    'ToolStreamHandler',
    'ToolStreamUpdate',
    'build_execution_context',
    # ── session ──
    'AgentSessionSnapshot',
    'AgentSessionState',
    # ── context ──
    'BudgetProjection',
    'CompactionResult',
    'ContextRunState',
    'PreModelBudgetGuard',
    'PreModelContextOutcome',
    'ReactiveCompactOutcome',
    'SessionMessageView',
    'SnipResult',
    # ── outcomes ──
    'AgentRunResult',
    'QueryServiceConfig',
    'QueryTurnResult',
    # ── interaction ──
    'EnvironmentLoadSummary',
    'SessionSummary',
    'SlashAutocompleteEntry',
    'SlashCommandContext',
    'SlashCommandResult',
    # ── planning ──
    'PlanStep',
    'PlanStepStatus',
    'TaskRecord',
    'TaskStatus',
    'WorkflowAction',
    'WorkflowLoadError',
    'WorkflowManifest',
    'WorkflowRunRecord',
    'WorkflowRunStatus',
    'WorkflowStepResult',
    'WorkflowStepSpec',
]
