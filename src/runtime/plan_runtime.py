"""ISSUE-018 Plan Runtime：计划存储、渲染与 plan-task 同步。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from core_contracts.protocol import JSONDict
from runtime.task_runtime import TaskRecord, TaskRuntime, TaskStatus


_PLAN_STATE_FILE = Path('.claw') / 'plan.json'
_SCHEMA_VERSION = 1


class PlanStepStatus(StrEnum):
    """计划步骤状态集合。"""

    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'


@dataclass(frozen=True)
class PlanStep:
    """单个计划步骤。"""

    step_id: str
    title: str
    description: str = ''
    dependencies: tuple[str, ...] = ()
    status: PlanStepStatus = PlanStepStatus.PENDING

    def to_dict(self) -> JSONDict:
        return {
            'step_id': self.step_id,
            'title': self.title,
            'description': self.description,
            'dependencies': list(self.dependencies),
            'status': self.status.value,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'PlanStep':
        data = dict(payload or {})
        step_id = _normalize_step_id(data.get('step_id', data.get('stepId', '')))
        title = str(data.get('title', '')).strip()
        if not title:
            raise ValueError(f'Plan step {step_id!r} requires non-empty title')
        return cls(
            step_id=step_id,
            title=title,
            description=str(data.get('description', '')).strip(),
            dependencies=_normalize_dependencies(data.get('dependencies', []), step_id=step_id),
            status=PlanStepStatus(str(data.get('status', PlanStepStatus.PENDING.value)).strip()),
        )


@dataclass
class PlanRuntime:
    """工作区本地计划运行时。"""

    workspace: Path
    steps_by_id: dict[str, PlanStep] = field(default_factory=dict)
    step_order: tuple[str, ...] = ()
    schema_version: int = _SCHEMA_VERSION

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'PlanRuntime':
        resolved_workspace = workspace.resolve()
        path = resolved_workspace / _PLAN_STATE_FILE
        if not path.is_file():
            return cls(workspace=resolved_workspace)

        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Plan runtime file {path} must contain a JSON object')

        steps_raw = payload.get('steps', [])
        if not isinstance(steps_raw, list):
            raise ValueError(f'Plan runtime file {path} field "steps" must be a JSON array')

        steps_by_id: dict[str, PlanStep] = {}
        step_order: list[str] = []
        for item in steps_raw:
            if not isinstance(item, dict):
                continue
            step = PlanStep.from_dict(item)
            if step.step_id in steps_by_id:
                raise ValueError(f'Duplicate plan step id in plan file: {step.step_id!r}')
            steps_by_id[step.step_id] = step
            step_order.append(step.step_id)

        runtime = cls(
            workspace=resolved_workspace,
            steps_by_id=steps_by_id,
            step_order=tuple(step_order),
            schema_version=_as_int(payload.get('schema_version'), _SCHEMA_VERSION),
        )
        runtime._validate_dependencies(runtime.list_steps())
        return runtime

    def save(self) -> Path:
        path = self.workspace / _PLAN_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    'schema_version': self.schema_version,
                    'steps': [item.to_dict() for item in self.list_steps()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        return path

    def list_steps(self) -> tuple[PlanStep, ...]:
        return tuple(
            self.steps_by_id[step_id]
            for step_id in self.step_order
            if step_id in self.steps_by_id
        )

    def get_step(self, step_id: str) -> PlanStep:
        normalized_id = _normalize_step_id(step_id)
        step = self.steps_by_id.get(normalized_id)
        if step is None:
            raise ValueError(f'Unknown plan step: {normalized_id!r}')
        return step

    def update_plan(
        self,
        steps: tuple[PlanStep, ...] | list[PlanStep],
        *,
        sync_tasks: bool = False,
    ) -> tuple[PlanStep, ...]:
        normalized_steps = self._normalize_steps(steps)
        self.steps_by_id = {item.step_id: item for item in normalized_steps}
        self.step_order = tuple(item.step_id for item in normalized_steps)
        self.save()
        if sync_tasks:
            return self.sync_tasks()
        return self.list_steps()

    def clear_plan(self, *, sync_tasks: bool = False) -> None:
        self.steps_by_id = {}
        self.step_order = ()
        self.save()
        if sync_tasks:
            TaskRuntime.from_workspace(self.workspace).replace_tasks(())

    def sync_tasks(self) -> tuple[PlanStep, ...]:
        task_runtime = TaskRuntime.from_workspace(self.workspace)
        existing_tasks = {item.task_id: item for item in task_runtime.list_tasks()}
        synced_tasks = task_runtime.replace_tasks(
            [self._step_to_task_record(step, existing_tasks.get(step.step_id)) for step in self.list_steps()]
        )
        task_index = {item.task_id: item for item in synced_tasks}

        updated_steps: dict[str, PlanStep] = {}
        for step in self.list_steps():
            synced_task = task_index.get(step.step_id)
            if synced_task is None:
                continue
            updated_steps[step.step_id] = replace(
                step,
                status=PlanStepStatus(synced_task.status.value),
            )

        self.steps_by_id = updated_steps
        self.step_order = tuple(item.step_id for item in self.list_steps())
        self.save()
        return self.list_steps()

    def render_plan(self) -> str:
        steps = self.list_steps()
        if not steps:
            return 'Plan Steps\n==========\n(none)'

        lines = ['Plan Steps', '==========']
        for index, step in enumerate(steps, start=1):
            lines.append(f'{index}. [{step.status.value}] {step.step_id} - {step.title}')
        return '\n'.join(lines)

    def _normalize_steps(self, steps: tuple[PlanStep, ...] | list[PlanStep]) -> tuple[PlanStep, ...]:
        normalized_steps: list[PlanStep] = []
        seen_ids: set[str] = set()
        for step in steps:
            if not isinstance(step, PlanStep):
                raise ValueError('update_plan expects PlanStep items')
            if step.step_id in seen_ids:
                raise ValueError(f'Duplicate plan step id: {step.step_id!r}')
            seen_ids.add(step.step_id)

            existing_step = self.steps_by_id.get(step.step_id)
            effective_status = existing_step.status if existing_step is not None else step.status
            normalized_steps.append(replace(step, status=effective_status))

        self._validate_dependencies(tuple(normalized_steps))
        return tuple(normalized_steps)

    @staticmethod
    def _validate_dependencies(steps: tuple[PlanStep, ...]) -> None:
        known_step_ids = {item.step_id for item in steps}
        for step in steps:
            missing_dependencies = [item for item in step.dependencies if item not in known_step_ids]
            if missing_dependencies:
                raise ValueError(
                    f'Plan step {step.step_id!r} references unknown dependencies: {", ".join(missing_dependencies)}'
                )

    @staticmethod
    def _step_to_task_record(step: PlanStep, existing_task: TaskRecord | None) -> TaskRecord:
        status = existing_task.status if existing_task is not None else TaskStatus.PENDING
        manual_block_reason = existing_task.manual_block_reason if existing_task is not None else None
        return TaskRecord(
            task_id=step.step_id,
            title=step.title,
            description=step.description,
            status=status,
            dependencies=step.dependencies,
            manual_block_reason=manual_block_reason,
        )


def _normalize_step_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('step_id must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError('step_id must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid step_id: {value!r}')
    return normalized


def _normalize_dependencies(value: object, *, step_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError('dependencies must be a list or tuple of step ids')

    normalized_dependencies: list[str] = []
    for item in value:
        dependency_id = _normalize_step_id(item)
        if dependency_id == step_id:
            raise ValueError(f'Plan step {step_id!r} cannot depend on itself')
        if dependency_id not in normalized_dependencies:
            normalized_dependencies.append(dependency_id)
    return tuple(normalized_dependencies)


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default