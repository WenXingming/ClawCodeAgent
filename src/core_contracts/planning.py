"""planning 领域跨模块公开契约。

本模块定义计划、任务与工作流在跨边界交互时允许暴露的稳定数据对象。
planning 网关以及其他外部调用方只能依赖这里的契约类型，不应直接触碰 planning 包内部运行时实现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core_contracts.primitives import JSONDict


class PlanStepStatus(StrEnum):
    """计划步骤的稳定状态集合。"""

    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'


@dataclass(frozen=True)
class PlanStep:
    """描述单个计划步骤的稳定契约。"""

    step_id: str  # str：计划步骤唯一标识。
    title: str  # str：计划步骤标题。
    description: str = ''  # str：计划步骤补充说明。
    dependencies: tuple[str, ...] = ()  # tuple[str, ...]：前置步骤 ID 集合。
    status: PlanStepStatus = PlanStepStatus.PENDING  # PlanStepStatus：计划步骤当前状态。

    def to_dict(self) -> JSONDict:
        """把计划步骤转换为 JSON 字典。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前步骤的可序列化表示。
        Raises:
            无。
        """
        return {
            'step_id': self.step_id,
            'title': self.title,
            'description': self.description,
            'dependencies': list(self.dependencies),
            'status': self.status.value,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'PlanStep':
        """从 JSON 字典恢复计划步骤。
        Args:
            payload (JSONDict | None): 原始 JSON 对象。
        Returns:
            PlanStep: 恢复后的计划步骤契约。
        Raises:
            ValueError: 当步骤字段非法时抛出。
        """
        data = dict(payload or {})
        step_id = _normalize_identifier(data.get('step_id', data.get('stepId', '')), label='step_id')
        title = _normalize_required_text(data.get('title'), label='title', owner=step_id)
        return cls(
            step_id=step_id,
            title=title,
            description=_normalize_optional_text(data.get('description')) or '',
            dependencies=_normalize_dependencies(data.get('dependencies'), label='step dependency', current_id=step_id),
            status=PlanStepStatus(str(data.get('status', PlanStepStatus.PENDING.value)).strip()),
        )


class TaskStatus(StrEnum):
    """任务的稳定状态集合。"""

    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'


@dataclass(frozen=True)
class TaskRecord:
    """描述单个任务的稳定契约。"""

    task_id: str  # str：任务唯一标识。
    title: str  # str：任务标题。
    description: str = ''  # str：任务补充说明。
    status: TaskStatus = TaskStatus.PENDING  # TaskStatus：任务当前状态。
    dependencies: tuple[str, ...] = ()  # tuple[str, ...]：任务声明的依赖集合。
    blocked_by: tuple[str, ...] = ()  # tuple[str, ...]：当前仍未满足的依赖集合。
    manual_block_reason: str | None = None  # str | None：人工阻塞原因。

    def to_dict(self) -> JSONDict:
        """把任务记录转换为 JSON 字典。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前任务记录的可序列化表示。
        Raises:
            无。
        """
        payload: JSONDict = {
            'task_id': self.task_id,
            'title': self.title,
            'description': self.description,
            'status': self.status.value,
            'dependencies': list(self.dependencies),
            'blocked_by': list(self.blocked_by),
        }
        if self.manual_block_reason is not None:
            payload['manual_block_reason'] = self.manual_block_reason
        return payload

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'TaskRecord':
        """从 JSON 字典恢复任务记录。
        Args:
            payload (JSONDict | None): 原始 JSON 对象。
        Returns:
            TaskRecord: 恢复后的任务契约。
        Raises:
            ValueError: 当任务字段非法时抛出。
        """
        data = dict(payload or {})
        task_id = _normalize_identifier(data.get('task_id', data.get('taskId', '')), label='task_id')
        title = _normalize_required_text(data.get('title'), label='title', owner=task_id)
        return cls(
            task_id=task_id,
            title=title,
            description=_normalize_optional_text(data.get('description')) or '',
            status=TaskStatus(str(data.get('status', TaskStatus.PENDING.value)).strip()),
            dependencies=_normalize_dependencies(data.get('dependencies'), label='task dependency', current_id=task_id),
            blocked_by=_normalize_dependencies(data.get('blocked_by', data.get('blockedBy')), label='blocked task'),
            manual_block_reason=_normalize_optional_text(
                data.get('manual_block_reason', data.get('manualBlockReason'))
            ),
        )


class WorkflowAction(StrEnum):
    """工作流步骤支持的动作集合。"""

    CREATE = 'create'
    UPDATE = 'update'
    START = 'start'
    COMPLETE = 'complete'
    BLOCK = 'block'
    CANCEL = 'cancel'


class WorkflowRunStatus(StrEnum):
    """工作流运行状态集合。"""

    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


@dataclass(frozen=True)
class WorkflowStepSpec:
    """描述单个工作流步骤的稳定契约。"""

    action: WorkflowAction  # WorkflowAction：当前步骤动作。
    task_id: str  # str：目标任务 ID。
    title: str | None = None  # str | None：创建或更新时使用的标题。
    description: str | None = None  # str | None：创建或更新时使用的描述。
    dependencies: tuple[str, ...] | None = None  # tuple[str, ...] | None：创建或更新时写入的依赖列表。
    reason: str | None = None  # str | None：阻塞动作附带的原因。

    def to_dict(self) -> JSONDict:
        """把工作流步骤转换为 JSON 字典。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前工作流步骤的可序列化表示。
        Raises:
            无。
        """
        payload: JSONDict = {
            'action': self.action.value,
            'task_id': self.task_id,
        }
        if self.title is not None:
            payload['title'] = self.title
        if self.description is not None:
            payload['description'] = self.description
        if self.dependencies is not None:
            payload['dependencies'] = list(self.dependencies)
        if self.reason is not None:
            payload['reason'] = self.reason
        return payload

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'WorkflowStepSpec':
        """从 JSON 字典恢复工作流步骤。
        Args:
            payload (JSONDict | None): 原始 JSON 对象。
        Returns:
            WorkflowStepSpec: 恢复后的工作流步骤契约。
        Raises:
            ValueError: 当步骤动作或字段非法时抛出。
        """
        data = dict(payload or {})
        action = WorkflowAction(str(data.get('action', '')).strip())
        task_id = _normalize_identifier(data.get('task_id', data.get('taskId', '')), label='task_id')
        title = _normalize_optional_text(data.get('title'))
        reason = _normalize_optional_text(data.get('reason'))
        if action is WorkflowAction.CREATE and not title:
            raise ValueError(f'Workflow create step for {task_id!r} requires title')
        if action is WorkflowAction.BLOCK and not reason:
            raise ValueError(f'Workflow block step for {task_id!r} requires reason')
        return cls(
            action=action,
            task_id=task_id,
            title=title,
            description=_normalize_optional_text(data.get('description')),
            dependencies=_normalize_optional_dependencies(data.get('dependencies')),
            reason=reason,
        )


@dataclass(frozen=True)
class WorkflowManifest:
    """描述单个工作流清单的稳定契约。"""

    workflow_id: str  # str：工作流唯一标识。
    title: str  # str：工作流标题。
    description: str = ''  # str：工作流补充说明。
    steps: tuple[WorkflowStepSpec, ...] = ()  # tuple[WorkflowStepSpec, ...]：按顺序执行的步骤集合。
    source_path: str | None = None  # str | None：来源文件的绝对路径字符串。

    def to_dict(self) -> JSONDict:
        """把工作流清单转换为 JSON 字典。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前工作流清单的可序列化表示。
        Raises:
            无。
        """
        return {
            'workflow_id': self.workflow_id,
            'title': self.title,
            'description': self.description,
            'steps': [item.to_dict() for item in self.steps],
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None, *, source_path: str | None = None) -> 'WorkflowManifest':
        """从 JSON 字典恢复工作流清单。
        Args:
            payload (JSONDict | None): 原始 JSON 对象。
            source_path (str | None): 来源文件路径字符串。
        Returns:
            WorkflowManifest: 恢复后的工作流清单契约。
        Raises:
            ValueError: 当工作流字段非法时抛出。
        """
        data = dict(payload or {})
        workflow_id = _normalize_identifier(data.get('workflow_id', data.get('workflowId', '')), label='workflow_id')
        title = _normalize_required_text(data.get('title'), label='title', owner=workflow_id)
        steps_raw = data.get('steps', [])
        if not isinstance(steps_raw, list):
            raise ValueError(f'Workflow {workflow_id!r} field "steps" must be a JSON array')
        steps = tuple(WorkflowStepSpec.from_dict(item) for item in steps_raw if isinstance(item, dict))
        if not steps:
            raise ValueError(f'Workflow {workflow_id!r} requires at least one step')
        return cls(
            workflow_id=workflow_id,
            title=title,
            description=_normalize_optional_text(data.get('description')) or '',
            steps=steps,
            source_path=source_path,
        )

    @classmethod
    def from_path(cls, manifest_path: str | Path) -> 'WorkflowManifest':
        """从磁盘文件加载工作流清单。
        Args:
            manifest_path (str | Path): 工作流清单文件路径。
        Returns:
            WorkflowManifest: 解析成功后的工作流清单契约。
        Raises:
            OSError: 当文件读取失败时抛出。
            ValueError: 当文件内容结构非法时抛出。
            json.JSONDecodeError: 当文件内容不是合法 JSON 时抛出。
        """
        path = Path(manifest_path).resolve()
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Workflow manifest {path} must contain a JSON object')
        return cls.from_dict(payload, source_path=str(path))


@dataclass(frozen=True)
class WorkflowLoadError:
    """描述工作流清单加载失败信息的稳定契约。"""

    workflow_id: str  # str：失败的工作流 ID 或文件 stem。
    error: str  # str：错误说明文本。
    source_path: str | None = None  # str | None：来源文件的绝对路径字符串。


@dataclass(frozen=True)
class WorkflowStepResult:
    """描述单个工作流步骤执行结果的稳定契约。"""

    step_index: int  # int：步骤的 1-based 顺序。
    action: WorkflowAction  # WorkflowAction：执行动作。
    task_id: str  # str：目标任务 ID。
    ok: bool  # bool：步骤是否成功执行。
    before_status: str | None = None  # str | None：执行前任务状态。
    after_status: str | None = None  # str | None：执行后任务状态。
    message: str = ''  # str：执行说明文本。
    error: str | None = None  # str | None：失败详情。

    def to_dict(self) -> JSONDict:
        """把步骤执行结果转换为 JSON 字典。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前结果的可序列化表示。
        Raises:
            无。
        """
        payload: JSONDict = {
            'step_index': self.step_index,
            'action': self.action.value,
            'task_id': self.task_id,
            'ok': self.ok,
            'message': self.message,
        }
        if self.before_status is not None:
            payload['before_status'] = self.before_status
        if self.after_status is not None:
            payload['after_status'] = self.after_status
        if self.error is not None:
            payload['error'] = self.error
        return payload

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'WorkflowStepResult':
        """从 JSON 字典恢复步骤执行结果。
        Args:
            payload (JSONDict | None): 原始 JSON 对象。
        Returns:
            WorkflowStepResult: 恢复后的步骤执行结果契约。
        Raises:
            ValueError: 当步骤结果字段非法时抛出。
        """
        data = dict(payload or {})
        return cls(
            step_index=_as_int(data.get('step_index', data.get('stepIndex')), 0),
            action=WorkflowAction(str(data.get('action', '')).strip()),
            task_id=_normalize_identifier(data.get('task_id', data.get('taskId', '')), label='task_id'),
            ok=bool(data.get('ok', False)),
            before_status=_normalize_optional_text(data.get('before_status', data.get('beforeStatus'))),
            after_status=_normalize_optional_text(data.get('after_status', data.get('afterStatus'))),
            message=_normalize_optional_text(data.get('message')) or '',
            error=_normalize_optional_text(data.get('error')),
        )


@dataclass(frozen=True)
class WorkflowRunRecord:
    """描述一次完整工作流运行结果的稳定契约。"""

    run_id: str  # str：运行唯一标识。
    workflow_id: str  # str：对应工作流 ID。
    status: WorkflowRunStatus  # WorkflowRunStatus：运行结束状态。
    started_at: str  # str：UTC ISO 时间戳。
    error_message: str | None = None  # str | None：整体失败说明。
    step_results: tuple[WorkflowStepResult, ...] = ()  # tuple[WorkflowStepResult, ...]：步骤执行结果集合。

    def to_dict(self) -> JSONDict:
        """把工作流运行记录转换为 JSON 字典。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前运行记录的可序列化表示。
        Raises:
            无。
        """
        payload: JSONDict = {
            'run_id': self.run_id,
            'workflow_id': self.workflow_id,
            'status': self.status.value,
            'started_at': self.started_at,
            'step_results': [item.to_dict() for item in self.step_results],
        }
        if self.error_message is not None:
            payload['error_message'] = self.error_message
        return payload

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'WorkflowRunRecord':
        """从 JSON 字典恢复工作流运行记录。
        Args:
            payload (JSONDict | None): 原始 JSON 对象。
        Returns:
            WorkflowRunRecord: 恢复后的工作流运行记录契约。
        Raises:
            ValueError: 当运行记录字段非法时抛出。
        """
        data = dict(payload or {})
        results_raw = data.get('step_results', data.get('stepResults', []))
        if not isinstance(results_raw, list):
            results_raw = []
        return cls(
            run_id=_normalize_identifier(data.get('run_id', data.get('runId', '')), label='run_id'),
            workflow_id=_normalize_identifier(data.get('workflow_id', data.get('workflowId', '')), label='workflow_id'),
            status=WorkflowRunStatus(str(data.get('status', '')).strip()),
            started_at=_normalize_required_text(data.get('started_at', data.get('startedAt')), label='started_at'),
            error_message=_normalize_optional_text(data.get('error_message', data.get('errorMessage'))),
            step_results=tuple(WorkflowStepResult.from_dict(item) for item in results_raw if isinstance(item, dict)),
        )


def _normalize_identifier(value: object, *, label: str) -> str:
    """规范化并校验标识符。
    Args:
        value (object): 待校验的原始值。
        label (str): 字段名。
    Returns:
        str: 去空白后的合法标识符。
    Raises:
        ValueError: 当值不是合法非空字符串时抛出。
    """
    if not isinstance(value, str):
        raise ValueError(f'{label} must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{label} must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid {label}: {value!r}')
    return normalized


def _normalize_required_text(value: object, *, label: str, owner: str | None = None) -> str:
    """规范化并校验必填文本。
    Args:
        value (object): 待校验的原始值。
        label (str): 字段名。
        owner (str | None): 用于构造错误信息的归属标识。
    Returns:
        str: 去空白后的非空字符串。
    Raises:
        ValueError: 当文本为空时抛出。
    """
    normalized = _normalize_optional_text(value)
    if normalized:
        return normalized
    if owner:
        raise ValueError(f'{owner!r} requires non-empty {label}')
    raise ValueError(f'{label} must not be empty')


def _normalize_optional_text(value: object) -> str | None:
    """把可选文本规范化为字符串或 None。
    Args:
        value (object): 待规范化的原始值。
    Returns:
        str | None: 去空白后的字符串；为空时返回 None。
    Raises:
        无。
    """
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_dependencies(
    value: object,
    *,
    label: str,
    current_id: str | None = None,
) -> tuple[str, ...]:
    """规范化依赖标识符集合。
    Args:
        value (object): 原始依赖值。
        label (str): 依赖字段标签。
        current_id (str | None): 当前对象 ID，用于阻止自依赖。
    Returns:
        tuple[str, ...]: 去重后的依赖标识符元组。
    Raises:
        ValueError: 当依赖值类型非法或存在自依赖时抛出。
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{label}s must be a list or tuple')
    normalized_dependencies: list[str] = []
    for item in value:
        dependency_id = _normalize_identifier(item, label=label)
        if current_id and dependency_id == current_id:
            raise ValueError(f'{current_id!r} cannot depend on itself')
        if dependency_id not in normalized_dependencies:
            normalized_dependencies.append(dependency_id)
    return tuple(normalized_dependencies)


def _normalize_optional_dependencies(value: object) -> tuple[str, ...] | None:
    """规范化可选依赖集合。
    Args:
        value (object): 原始依赖值。
    Returns:
        tuple[str, ...] | None: 规范化后的依赖元组；未提供时返回 None。
    Raises:
        ValueError: 当依赖值类型非法时抛出。
    """
    if value is None:
        return None
    return _normalize_dependencies(value, label='dependency')


def _as_int(value: object, default: int) -> int:
    """把输入值安全转换为整数。
    Args:
        value (object): 待转换的原始值。
        default (int): 转换失败时的默认值。
    Returns:
        int: 转换后的整数或默认值。
    Raises:
        无。
    """
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
