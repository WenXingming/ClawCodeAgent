"""ISSUE-017 Task Runtime：任务状态机、本地持久化与依赖解析。"""

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
    """单个任务的稳定表示。"""

    task_id: str
    title: str
    description: str = ''
    status: TaskStatus = TaskStatus.PENDING
    dependencies: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    manual_block_reason: str | None = None

    def to_dict(self) -> JSONDict:
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
    """工作区本地任务运行时。"""

    workspace: Path
    tasks_by_id: dict[str, TaskRecord] = field(default_factory=dict)
    task_order: tuple[str, ...] = ()
    schema_version: int = _SCHEMA_VERSION

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'TaskRuntime':
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
        return tuple(
            self.tasks_by_id[task_id]
            for task_id in self.task_order
            if task_id in self.tasks_by_id
        )

    def next_tasks(self) -> tuple[TaskRecord, ...]:
        return tuple(
            task
            for task in self.list_tasks()
            if task.status is TaskStatus.PENDING
        )

    def get_task(self, task_id: str) -> TaskRecord:
        normalized_id = _normalize_task_id(task_id)
        task = self.tasks_by_id.get(normalized_id)
        if task is None:
            raise ValueError(f'Unknown task: {normalized_id!r}')
        return task

    def _refreshed_task(self, task_id: str) -> TaskRecord:
        refreshed = self._reconcile_dependency_states(self.tasks_by_id)
        self.tasks_by_id = refreshed
        return self.get_task(task_id)

    def _commit(
        self,
        tasks_by_id: dict[str, TaskRecord],
        *,
        task_order: tuple[str, ...] | None = None,
    ) -> None:
        if task_order is not None:
            self.task_order = task_order
        self.tasks_by_id = self._reconcile_dependency_states(tasks_by_id)
        self.save()

    def _reconcile_dependency_states(
        self,
        tasks_by_id: dict[str, TaskRecord],
    ) -> dict[str, TaskRecord]:
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
    if not isinstance(value, str):
        raise ValueError('task_id must be a string')

    normalized = value.strip()
    if not normalized:
        raise ValueError('task_id must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid task_id: {value!r}')
    return normalized


def _normalize_dependencies(value: object, *, task_id: str) -> tuple[str, ...]:
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
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default