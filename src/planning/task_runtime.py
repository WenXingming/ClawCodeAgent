"""管理工作区任务状态机、依赖阻塞与持久化。

本模块只负责 planning 域内部的任务存储和状态流转规则。
外部调用方必须通过 PlanningGateway 访问这些能力，而不是直接依赖本文件中的 runtime 实现。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from core_contracts.planning import TaskRecord, TaskStatus


_TASKS_STATE_FILE = Path('.claw') / 'tasks.json'
_SCHEMA_VERSION = 1


class TaskRuntime:
    """管理工作区本地任务状态。

    核心工作流：
    1. `from_workspace` 读取 `.claw/tasks.json` 并恢复顺序。
    2. 通过公开方法驱动任务创建、更新和状态迁移。
    3. `_commit` 统一重算依赖阻塞状态并持久化保存。
    """

    def __init__(
        self,
        workspace: Path,
        *,
        tasks_by_id: dict[str, TaskRecord] | None = None,
        task_order: tuple[str, ...] = (),
        schema_version: int = _SCHEMA_VERSION,
    ) -> None:
        """初始化任务 runtime。
        Args:
            workspace (Path): 工作区根目录。
            tasks_by_id (dict[str, TaskRecord] | None): 当前任务索引。
            task_order (tuple[str, ...]): 当前任务顺序。
            schema_version (int): 当前状态文件版本。
        Returns:
            None: 该方法仅负责保存状态。
        Raises:
            ValueError: 当工作区路径不存在时抛出。
        """
        resolved_workspace = workspace.resolve()
        if not resolved_workspace.is_dir():
            raise ValueError(f'Workspace directory does not exist: {resolved_workspace}')
        self.workspace = resolved_workspace  # Path：当前任务 runtime 绑定的工作区根目录。
        self.tasks_by_id = dict(tasks_by_id or {})  # dict[str, TaskRecord]：按任务 ID 建立的任务索引。
        self.task_order = tuple(task_order)  # tuple[str, ...]：任务的稳定展示顺序。
        self.schema_version = schema_version  # int：当前任务状态文件版本号。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'TaskRuntime':
        """从工作区加载任务状态。
        Args:
            workspace (Path): 工作区根目录。
        Returns:
            TaskRuntime: 初始化完成的任务 runtime。
        Raises:
            ValueError: 当任务文件结构非法时抛出。
        """
        resolved_workspace = workspace.resolve()
        path = resolved_workspace / _TASKS_STATE_FILE
        if not path.is_file():
            return cls(resolved_workspace)
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Task runtime file {path} must contain a JSON object')
        tasks_raw = payload.get('tasks', [])
        if not isinstance(tasks_raw, list):
            raise ValueError(f'Task runtime file {path} field "tasks" must be a JSON array')
        tasks_by_id: dict[str, TaskRecord] = {}
        task_order: list[str] = []
        for item in tasks_raw:
            if not isinstance(item, dict):
                continue
            task = TaskRecord.from_dict(item)
            if task.task_id in tasks_by_id:
                raise ValueError(f'Duplicate task id in task runtime file: {task.task_id!r}')
            tasks_by_id[task.task_id] = task
            task_order.append(task.task_id)
        runtime = cls(
            resolved_workspace,
            tasks_by_id=tasks_by_id,
            task_order=tuple(task_order),
            schema_version=_as_int(payload.get('schema_version'), _SCHEMA_VERSION),
        )
        runtime.tasks_by_id = runtime._reconcile_dependency_states(runtime.tasks_by_id)
        return runtime

    def save(self) -> None:
        """把当前任务状态写回工作区文件。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            None: 该方法原地落盘保存状态。
        Raises:
            OSError: 当文件写入失败时抛出。
        """
        path = self.workspace / _TASKS_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    'schema_version': self.schema_version,
                    'tasks': [task.to_dict() for task in self.list_tasks()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

    def create_task(
        self,
        task_id: str,
        title: str,
        *,
        description: str = '',
        dependencies: tuple[str, ...] | list[str] = (),
    ) -> TaskRecord:
        """创建新任务。
        Args:
            task_id (str): 新任务 ID。
            title (str): 新任务标题。
            description (str): 新任务描述。
            dependencies (tuple[str, ...] | list[str]): 依赖任务 ID 集合。
        Returns:
            TaskRecord: 创建后的任务契约。
        Raises:
            ValueError: 当任务参数非法时抛出。
        """
        normalized_id = _normalize_identifier(task_id, label='task_id')
        if normalized_id in self.tasks_by_id:
            raise ValueError(f'Task already exists: {normalized_id!r}')
        normalized_title = _normalize_required_text(title, label='title')
        created_task = TaskRecord(
            task_id=normalized_id,
            title=normalized_title,
            description=str(description).strip(),
            dependencies=_normalize_dependencies(dependencies, current_id=normalized_id),
        )
        updated_tasks = dict(self.tasks_by_id)
        updated_tasks[normalized_id] = created_task
        self._commit(updated_tasks, task_order=self.task_order + (normalized_id,))
        return self.tasks_by_id[normalized_id]

    def replace_tasks(self, tasks: tuple[TaskRecord, ...] | list[TaskRecord]) -> tuple[TaskRecord, ...]:
        """用新任务集合整体替换当前状态。
        Args:
            tasks (tuple[TaskRecord, ...] | list[TaskRecord]): 新任务集合。
        Returns:
            tuple[TaskRecord, ...]: 替换后的任务集合。
        Raises:
            ValueError: 当任务集合非法时抛出。
        """
        normalized_tasks: list[TaskRecord] = []
        seen_ids: set[str] = set()
        for task in tasks:
            if not isinstance(task, TaskRecord):
                raise ValueError('replace_tasks expects TaskRecord items')
            if task.task_id in seen_ids:
                raise ValueError(f'Duplicate task id in replace_tasks: {task.task_id!r}')
            seen_ids.add(task.task_id)
            normalized_tasks.append(task)
        self._commit(
            {task.task_id: task for task in normalized_tasks},
            task_order=tuple(task.task_id for task in normalized_tasks),
        )
        return self.list_tasks()

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        dependencies: tuple[str, ...] | list[str] | None = None,
    ) -> TaskRecord:
        """更新现有任务的基础字段。
        Args:
            task_id (str): 目标任务 ID。
            title (str | None): 可选新标题。
            description (str | None): 可选新描述。
            dependencies (tuple[str, ...] | list[str] | None): 可选新依赖集合。
        Returns:
            TaskRecord: 更新后的任务契约。
        Raises:
            ValueError: 当任务不存在或更新内容非法时抛出。
        """
        current_task = self.get_task(task_id)
        updated_task = replace(
            current_task,
            title=_normalize_required_text(title, label='title') if title is not None else current_task.title,
            description=str(description).strip() if description is not None else current_task.description,
            dependencies=(
                _normalize_dependencies(dependencies, current_id=current_task.task_id)
                if dependencies is not None
                else current_task.dependencies
            ),
        )
        updated_tasks = dict(self.tasks_by_id)
        updated_tasks[current_task.task_id] = updated_task
        self._commit(updated_tasks)
        return self.tasks_by_id[current_task.task_id]

    def start_task(self, task_id: str) -> TaskRecord:
        """把任务切换为进行中。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 更新后的任务契约。
        Raises:
            ValueError: 当任务状态不允许启动时抛出。
        """
        current_task = self._refreshed_task(task_id)
        if current_task.status is not TaskStatus.PENDING:
            raise ValueError(f'Task {current_task.task_id!r} cannot start from status {current_task.status.value!r}')
        updated_tasks = dict(self.tasks_by_id)
        updated_tasks[current_task.task_id] = replace(current_task, status=TaskStatus.IN_PROGRESS, blocked_by=())
        self._commit(updated_tasks)
        return self.tasks_by_id[current_task.task_id]

    def complete_task(self, task_id: str) -> TaskRecord:
        """把任务标记为已完成。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 更新后的任务契约。
        Raises:
            ValueError: 当任务状态不允许完成时抛出。
        """
        current_task = self.get_task(task_id)
        if current_task.status is not TaskStatus.IN_PROGRESS:
            raise ValueError(f'Task {current_task.task_id!r} cannot complete from status {current_task.status.value!r}')
        updated_tasks = dict(self.tasks_by_id)
        updated_tasks[current_task.task_id] = replace(
            current_task,
            status=TaskStatus.COMPLETED,
            blocked_by=(),
            manual_block_reason=None,
        )
        self._commit(updated_tasks)
        return self.tasks_by_id[current_task.task_id]

    def block_task(self, task_id: str, *, reason: str) -> TaskRecord:
        """把任务显式标记为阻塞。
        Args:
            task_id (str): 目标任务 ID。
            reason (str): 阻塞原因。
        Returns:
            TaskRecord: 更新后的任务契约。
        Raises:
            ValueError: 当阻塞输入非法时抛出。
        """
        current_task = self.get_task(task_id)
        if current_task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise ValueError(f'Task {current_task.task_id!r} cannot be blocked from status {current_task.status.value!r}')
        normalized_reason = _normalize_required_text(reason, label='reason')
        updated_tasks = dict(self.tasks_by_id)
        updated_tasks[current_task.task_id] = replace(
            current_task,
            status=TaskStatus.BLOCKED,
            manual_block_reason=normalized_reason,
        )
        self._commit(updated_tasks)
        return self.tasks_by_id[current_task.task_id]

    def cancel_task(self, task_id: str) -> TaskRecord:
        """把任务标记为已取消。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 更新后的任务契约。
        Raises:
            ValueError: 当任务状态不允许取消时抛出。
        """
        current_task = self.get_task(task_id)
        if current_task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise ValueError(f'Task {current_task.task_id!r} cannot cancel from status {current_task.status.value!r}')
        updated_tasks = dict(self.tasks_by_id)
        updated_tasks[current_task.task_id] = replace(
            current_task,
            status=TaskStatus.CANCELLED,
            blocked_by=(),
            manual_block_reason=None,
        )
        self._commit(updated_tasks)
        return self.tasks_by_id[current_task.task_id]

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        """按稳定顺序返回全部任务记录。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[TaskRecord, ...]: 当前任务集合。
        Raises:
            无。
        """
        return tuple(self.tasks_by_id[task_id] for task_id in self.task_order if task_id in self.tasks_by_id)

    def next_tasks(self) -> tuple[TaskRecord, ...]:
        """返回当前可执行的待处理任务集合。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[TaskRecord, ...]: 当前状态为 pending 的任务集合。
        Raises:
            无。
        """
        return tuple(task for task in self.list_tasks() if task.status is TaskStatus.PENDING)

    def get_task(self, task_id: str) -> TaskRecord:
        """按任务 ID 返回单个任务记录。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 对应的任务契约。
        Raises:
            ValueError: 当任务不存在或 ID 非法时抛出。
        """
        normalized_id = _normalize_identifier(task_id, label='task_id')
        task = self.tasks_by_id.get(normalized_id)
        if task is None:
            raise ValueError(f'Unknown task: {normalized_id!r}')
        return task

    def _refreshed_task(self, task_id: str) -> TaskRecord:
        """在读取任务前基于当前依赖状态刷新阻塞信息。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 刷新后的任务契约。
        Raises:
            ValueError: 当任务不存在时抛出。
        """
        self.tasks_by_id = self._reconcile_dependency_states(self.tasks_by_id)
        return self.get_task(task_id)

    def _commit(
        self,
        tasks_by_id: dict[str, TaskRecord],
        *,
        task_order: tuple[str, ...] | None = None,
    ) -> None:
        """统一提交任务状态变更并持久化保存。
        Args:
            tasks_by_id (dict[str, TaskRecord]): 变更后的任务索引。
            task_order (tuple[str, ...] | None): 可选新任务顺序。
        Returns:
            None: 该方法原地更新并落盘保存。
        Raises:
            OSError: 当文件写入失败时抛出。
        """
        if task_order is not None:
            self.task_order = task_order
        self.tasks_by_id = self._reconcile_dependency_states(tasks_by_id)
        self.save()

    def _reconcile_dependency_states(self, tasks_by_id: dict[str, TaskRecord]) -> dict[str, TaskRecord]:
        """根据依赖完成情况重算任务阻塞状态。
        Args:
            tasks_by_id (dict[str, TaskRecord]): 待重算的任务索引。
        Returns:
            dict[str, TaskRecord]: 重算后的任务索引。
        Raises:
            无。
        """
        completed_tasks = {
            task_id
            for task_id, task in tasks_by_id.items()
            if task.status is TaskStatus.COMPLETED
        }
        reconciled: dict[str, TaskRecord] = {}
        for task_id in self.task_order:
            task = tasks_by_id.get(task_id)
            if task is None:
                continue
            if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                reconciled[task_id] = replace(task, blocked_by=())
                continue
            unresolved_dependencies = tuple(
                dependency_id
                for dependency_id in task.dependencies
                if dependency_id not in completed_tasks
            )
            if task.manual_block_reason:
                reconciled[task_id] = replace(
                    task,
                    status=TaskStatus.BLOCKED,
                    blocked_by=unresolved_dependencies,
                )
                continue
            if task.status is TaskStatus.IN_PROGRESS and not unresolved_dependencies:
                reconciled[task_id] = replace(task, blocked_by=())
                continue
            if unresolved_dependencies:
                reconciled[task_id] = replace(
                    task,
                    status=TaskStatus.BLOCKED,
                    blocked_by=unresolved_dependencies,
                )
                continue
            next_status = TaskStatus.PENDING if task.status is TaskStatus.BLOCKED else task.status
            reconciled[task_id] = replace(task, status=next_status, blocked_by=())
        return reconciled


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


def _normalize_required_text(value: object, *, label: str) -> str:
    """规范化并校验必填文本。
    Args:
        value (object): 待校验的原始值。
        label (str): 字段名。
    Returns:
        str: 去空白后的非空字符串。
    Raises:
        ValueError: 当文本为空时抛出。
    """
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f'{label} must not be empty')
    return normalized


def _normalize_dependencies(value: object, *, current_id: str) -> tuple[str, ...]:
    """规范化任务依赖集合。
    Args:
        value (object): 原始依赖值。
        current_id (str): 当前任务 ID，用于阻止自依赖。
    Returns:
        tuple[str, ...]: 去重后的依赖元组。
    Raises:
        ValueError: 当依赖值类型非法或存在自依赖时抛出。
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError('dependencies must be a list or tuple of task ids')
    normalized_dependencies: list[str] = []
    for item in value:
        dependency_id = _normalize_identifier(item, label='task dependency')
        if dependency_id == current_id:
            raise ValueError(f'Task {current_id!r} cannot depend on itself')
        if dependency_id not in normalized_dependencies:
            normalized_dependencies.append(dependency_id)
    return tuple(normalized_dependencies)


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
