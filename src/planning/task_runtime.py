"""管理工作区本地任务状态机、持久化与依赖解析。

本模块负责把任务的创建、更新、状态流转和依赖阻塞规则收敛到一个本地运行时对象中，并把状态稳定持久化到 `.claw/tasks.json`。上层通常通过 `TaskRuntime.from_workspace()` 加载状态，再调用公开方法驱动任务状态变化。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from core_contracts.protocol import JSONDict


_TASKS_STATE_FILE = Path('.claw') / 'tasks.json'
_SCHEMA_VERSION = 1


class TaskStatus(StrEnum):
    """任务的稳定状态集合。"""

    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'


@dataclass(frozen=True)
class TaskRecord:
    """表示单个任务的稳定记录。

    该对象是任务在内存与 JSON 持久化中的统一表示，保存任务标题、描述、状态、依赖关系以及阻塞原因等核心信息。
    """

    task_id: str  # str：任务的稳定唯一标识。
    title: str  # str：任务展示标题。
    description: str = ''  # str：任务的补充说明文本。
    status: TaskStatus = TaskStatus.PENDING  # TaskStatus：任务当前状态。
    dependencies: tuple[str, ...] = ()  # tuple[str, ...]：当前任务依赖的上游任务 ID 列表。
    blocked_by: tuple[str, ...] = ()  # tuple[str, ...]：当前仍未满足的依赖任务 ID 列表。
    manual_block_reason: str | None = None  # str | None：人工阻塞任务时记录的原因。

    def to_dict(self) -> JSONDict:
        """把任务记录转换成可持久化的 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前任务记录的可序列化字典表示。
        """
        payload: JSONDict = {
            'task_id': self.task_id,
            'title': self.title,
            'description': self.description,
            'status': self.status.value,
            'dependencies': list(self.dependencies),
            'blocked_by': list(self.blocked_by),
        }
        if self.manual_block_reason:
            payload['manual_block_reason'] = self.manual_block_reason
        return payload

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'TaskRecord':
        """从 JSON 字典恢复单个任务记录。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            TaskRecord: 恢复后的任务记录对象。
        Raises:
            ValueError: 当任务 ID、标题或状态非法时抛出。
        """
        data = dict(payload or {})
        task_id = _normalize_task_id(data.get('task_id', data.get('taskId', '')))
        title = str(data.get('title', '')).strip()
        if not title:
            raise ValueError(f'Task {task_id!r} requires non-empty title')

        status = TaskStatus(str(data.get('status', TaskStatus.PENDING.value)).strip())
        dependencies = _normalize_dependencies(data.get('dependencies', []), task_id=task_id)
        blocked_by = _normalize_dependencies(data.get('blocked_by', data.get('blockedBy', [])), task_id='')
        manual_block_reason = _normalize_optional_text(
            data.get('manual_block_reason', data.get('manualBlockReason'))
        )
        return cls(
            task_id=task_id,
            title=title,
            description=str(data.get('description', '')).strip(),
            status=status,
            dependencies=dependencies,
            blocked_by=blocked_by,
            manual_block_reason=manual_block_reason,
        )


@dataclass
class TaskRuntime:
    """管理工作区本地任务状态的运行时对象。

    典型工作流如下：
    1. 调用 `from_workspace()` 从 `.claw/tasks.json` 加载当前状态。
    2. 通过 `create_task()`、`update_task()`、`start_task()` 等公开方法驱动任务状态变化。
    3. 每次状态变更后由 `_commit()` 统一做依赖重算并持久化保存。
    """

    workspace: Path  # Path：当前任务运行时所属的工作区根目录。
    tasks_by_id: dict[str, TaskRecord] = field(default_factory=dict)  # dict[str, TaskRecord]：按任务 ID 建立的任务索引。
    task_order: tuple[str, ...] = ()  # tuple[str, ...]：任务的稳定展示顺序。
    schema_version: int = _SCHEMA_VERSION  # int：当前任务状态文件使用的 schema 版本。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'TaskRuntime':
        """从工作区加载任务运行时状态。

        Args:
            workspace (Path): 工作区根目录。

        Returns:
            TaskRuntime: 解析并校验后的任务运行时对象。
        Raises:
            ValueError: 当任务状态文件结构非法、字段类型错误或存在重复任务 ID 时抛出。
        """
        resolved_workspace = workspace.resolve()
        path = resolved_workspace / _TASKS_STATE_FILE
        if not path.is_file():
            return cls(workspace=resolved_workspace)

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
            workspace=resolved_workspace,
            tasks_by_id=tasks_by_id,
            task_order=tuple(task_order),
            schema_version=_as_int(payload.get('schema_version'), _SCHEMA_VERSION),
        )
        runtime.tasks_by_id = runtime._reconcile_dependency_states(tasks_by_id)
        return runtime

    def save(self) -> Path:
        """把当前任务状态保存到工作区文件。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            Path: 实际写入的任务状态文件路径。
        """
        path = self.workspace / _TASKS_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    'schema_version': self.schema_version,
                    'tasks': [item.to_dict() for item in self.list_tasks()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        return path

    def create_task(
        self,
        task_id: str,
        title: str,
        *,
        description: str = '',
        dependencies: tuple[str, ...] | list[str] = (),
    ) -> TaskRecord:
        """创建一条新的任务记录。

        Args:
            task_id (str): 新任务的唯一标识。
            title (str): 新任务的展示标题。
            description (str): 新任务的补充说明。
            dependencies (tuple[str, ...] | list[str]): 新任务依赖的上游任务 ID 列表。
        Returns:
            TaskRecord: 创建并持久化后的任务记录。
        Raises:
            ValueError: 当任务 ID 重复、标题为空或依赖非法时抛出。
        """
        normalized_id = _normalize_task_id(task_id)
        if normalized_id in self.tasks_by_id:
            raise ValueError(f'Task already exists: {normalized_id!r}')

        normalized_title = str(title).strip()
        if not normalized_title:
            raise ValueError('Task title must not be empty')

        task = TaskRecord(
            task_id=normalized_id,
            title=normalized_title,
            description=str(description).strip(),
            dependencies=_normalize_dependencies(dependencies, task_id=normalized_id),
        )
        tasks_by_id = dict(self.tasks_by_id)
        tasks_by_id[normalized_id] = task
        self._commit(tasks_by_id, task_order=self.task_order + (normalized_id,))
        return self.tasks_by_id[normalized_id]

    def replace_tasks(self, tasks: tuple[TaskRecord, ...] | list[TaskRecord]) -> tuple[TaskRecord, ...]:
        """用一组任务记录整体替换当前任务集。

        Args:
            tasks (tuple[TaskRecord, ...] | list[TaskRecord]): 需要整体替换为当前状态的任务记录集合。
        Returns:
            tuple[TaskRecord, ...]: 替换并持久化后的任务列表。
        Raises:
            ValueError: 当输入中存在非 `TaskRecord` 项或重复任务 ID 时抛出。
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
            {item.task_id: item for item in normalized_tasks},
            task_order=tuple(item.task_id for item in normalized_tasks),
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
            task_id (str): 需要更新的任务 ID。
            title (str | None): 新标题；为 None 时保持原值。
            description (str | None): 新描述；为 None 时保持原值。
            dependencies (tuple[str, ...] | list[str] | None): 新依赖集合；为 None 时保持原值。
        Returns:
            TaskRecord: 更新并持久化后的任务记录。
        Raises:
            ValueError: 当任务不存在、标题为空或依赖非法时抛出。
        """
        current = self.get_task(task_id)
        updated = replace(
            current,
            title=str(title).strip() if title is not None else current.title,
            description=str(description).strip() if description is not None else current.description,
            dependencies=(
                _normalize_dependencies(dependencies, task_id=current.task_id)
                if dependencies is not None
                else current.dependencies
            ),
        )
        if not updated.title:
            raise ValueError('Task title must not be empty')

        tasks_by_id = dict(self.tasks_by_id)
        tasks_by_id[current.task_id] = updated
        self._commit(tasks_by_id)
        return self.tasks_by_id[current.task_id]

    def start_task(self, task_id: str) -> TaskRecord:
        """把任务从可执行状态切换为进行中。

        Args:
            task_id (str): 需要启动的任务 ID。
        Returns:
            TaskRecord: 启动并持久化后的任务记录。
        Raises:
            ValueError: 当任务不存在、仍被依赖阻塞或当前状态不允许启动时抛出。
        """
        current = self._refreshed_task(task_id)
        if current.status is not TaskStatus.PENDING:
            raise ValueError(f'Task {current.task_id!r} cannot start from status {current.status.value!r}')

        tasks_by_id = dict(self.tasks_by_id)
        tasks_by_id[current.task_id] = replace(
            current,
            status=TaskStatus.IN_PROGRESS,
            blocked_by=(),
        )
        self._commit(tasks_by_id)
        return self.tasks_by_id[current.task_id]

    def complete_task(self, task_id: str) -> TaskRecord:
        """把进行中的任务标记为已完成。

        Args:
            task_id (str): 需要完成的任务 ID。
        Returns:
            TaskRecord: 完成并持久化后的任务记录。
        Raises:
            ValueError: 当任务不存在或当前状态不允许完成时抛出。
        """
        current = self.get_task(task_id)
        if current.status is not TaskStatus.IN_PROGRESS:
            raise ValueError(f'Task {current.task_id!r} cannot complete from status {current.status.value!r}')

        tasks_by_id = dict(self.tasks_by_id)
        tasks_by_id[current.task_id] = replace(
            current,
            status=TaskStatus.COMPLETED,
            blocked_by=(),
            manual_block_reason=None,
        )
        self._commit(tasks_by_id)
        return self.tasks_by_id[current.task_id]

    def block_task(self, task_id: str, *, reason: str) -> TaskRecord:
        """把任务显式标记为阻塞，并记录人工阻塞原因。

        Args:
            task_id (str): 需要阻塞的任务 ID。
            reason (str): 人工阻塞的说明文本。
        Returns:
            TaskRecord: 阻塞并持久化后的任务记录。
        Raises:
            ValueError: 当任务不存在、当前状态不允许阻塞或原因为空时抛出。
        """
        current = self.get_task(task_id)
        if current.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise ValueError(f'Task {current.task_id!r} cannot be blocked from status {current.status.value!r}')

        normalized_reason = str(reason).strip()
        if not normalized_reason:
            raise ValueError('Block reason must not be empty')

        tasks_by_id = dict(self.tasks_by_id)
        tasks_by_id[current.task_id] = replace(
            current,
            status=TaskStatus.BLOCKED,
            manual_block_reason=normalized_reason,
        )
        self._commit(tasks_by_id)
        return self.tasks_by_id[current.task_id]

    def cancel_task(self, task_id: str) -> TaskRecord:
        """把任务标记为已取消。

        Args:
            task_id (str): 需要取消的任务 ID。
        Returns:
            TaskRecord: 取消并持久化后的任务记录。
        Raises:
            ValueError: 当任务不存在或当前状态不允许取消时抛出。
        """
        current = self.get_task(task_id)
        if current.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise ValueError(f'Task {current.task_id!r} cannot cancel from status {current.status.value!r}')

        tasks_by_id = dict(self.tasks_by_id)
        tasks_by_id[current.task_id] = replace(
            current,
            status=TaskStatus.CANCELLED,
            blocked_by=(),
            manual_block_reason=None,
        )
        self._commit(tasks_by_id)
        return self.tasks_by_id[current.task_id]

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        """按稳定顺序返回全部任务记录。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[TaskRecord, ...]: 当前任务列表的只读视图。
        """
        return tuple(
            self.tasks_by_id[task_id]
            for task_id in self.task_order
            if task_id in self.tasks_by_id
        )

    def next_tasks(self) -> tuple[TaskRecord, ...]:
        """返回当前处于待处理状态的任务集合。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[TaskRecord, ...]: 当前状态为 `pending` 的任务列表。
        """
        return tuple(
            task
            for task in self.list_tasks()
            if task.status is TaskStatus.PENDING
        )

    def get_task(self, task_id: str) -> TaskRecord:
        """按任务 ID 获取单条任务记录。

        Args:
            task_id (str): 需要读取的任务 ID。
        Returns:
            TaskRecord: 找到的任务记录。
        Raises:
            ValueError: 当任务不存在或任务 ID 非法时抛出。
        """
        normalized_id = _normalize_task_id(task_id)
        task = self.tasks_by_id.get(normalized_id)
        if task is None:
            raise ValueError(f'Unknown task: {normalized_id!r}')
        return task

    def _refreshed_task(self, task_id: str) -> TaskRecord:
        """在读取任务前先基于当前依赖状态刷新阻塞信息。

        Args:
            task_id (str): 需要读取的任务 ID。
        Returns:
            TaskRecord: 依赖状态刷新后的任务记录。
        """
        refreshed = self._reconcile_dependency_states(self.tasks_by_id)
        self.tasks_by_id = refreshed
        return self.get_task(task_id)

    def _commit(
        self,
        tasks_by_id: dict[str, TaskRecord],
        *,
        task_order: tuple[str, ...] | None = None,
    ) -> None:
        """统一提交任务状态变更并持久化保存。

        Args:
            tasks_by_id (dict[str, TaskRecord]): 变更后的任务索引快照。
            task_order (tuple[str, ...] | None): 可选的新任务顺序；为 None 时保留原顺序。
        Returns:
            None: 该方法原地更新运行时并落盘保存。
        """
        if task_order is not None:
            self.task_order = task_order
        self.tasks_by_id = self._reconcile_dependency_states(tasks_by_id)
        self.save()

    def _reconcile_dependency_states(
        self,
        tasks_by_id: dict[str, TaskRecord],
    ) -> dict[str, TaskRecord]:
        """根据依赖完成情况重算任务阻塞状态。

        Args:
            tasks_by_id (dict[str, TaskRecord]): 需要参与重算的任务索引。
        Returns:
            dict[str, TaskRecord]: 经过阻塞状态重算后的新任务索引。
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
            reconciled[task_id] = replace(
                task,
                status=next_status,
                blocked_by=(),
            )

        return reconciled


def _normalize_task_id(value: object) -> str:
    """规范化并校验任务 ID。

    Args:
        value (object): 待校验的原始任务 ID。
    Returns:
        str: 去除首尾空白后的合法任务 ID。
    Raises:
        ValueError: 当任务 ID 不是字符串、为空或包含非法路径成分时抛出。
    """
    if not isinstance(value, str):
        raise ValueError('task_id must be a string')

    normalized = value.strip()
    if not normalized:
        raise ValueError('task_id must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid task_id: {value!r}')
    return normalized


def _normalize_dependencies(value: object, *, task_id: str) -> tuple[str, ...]:
    """规范化任务依赖列表。

    Args:
        value (object): 待校验的依赖列表原始值。
        task_id (str): 当前任务 ID，用于阻止任务依赖自身。
    Returns:
        tuple[str, ...]: 去重并规范化后的依赖任务 ID 元组。
    Raises:
        ValueError: 当依赖列表类型非法、依赖 ID 非法或任务依赖自身时抛出。
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError('dependencies must be a list or tuple of task ids')

    normalized_dependencies: list[str] = []
    for item in value:
        dependency_id = _normalize_task_id(item)
        if task_id and dependency_id == task_id:
            raise ValueError(f'Task {task_id!r} cannot depend on itself')
        if dependency_id not in normalized_dependencies:
            normalized_dependencies.append(dependency_id)
    return tuple(normalized_dependencies)


def _normalize_optional_text(value: object) -> str | None:
    """把可选文本输入规范化为字符串或 None。

    Args:
        value (object): 待规范化的原始输入值。
    Returns:
        str | None: 去空白后的字符串；若为空则返回 None。
    """
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _as_int(value: object, default: int) -> int:
    """把输入值安全转换为整数。

    Args:
        value (object): 待转换的原始值。
        default (int): 转换失败或输入无效时返回的默认值。
    Returns:
        int: 转换后的整数；失败时返回默认值。
    """
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default