"""ISSUE-019 Workflow Runtime：工作流定义读取、运行与历史记录。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from core_contracts.protocol import JSONDict
from planning.task_runtime import TaskRecord, TaskRuntime


_WORKFLOW_MANIFEST_FILE = Path('.claw') / 'workflows.json'
_WORKFLOW_MANIFEST_DIR = Path('.claw') / 'workflows'
_WORKFLOW_RUN_HISTORY_FILE = Path('.claw') / 'workflow_runs.json'
_SCHEMA_VERSION = 1


class WorkflowAction(StrEnum):
    """工作流步骤支持的动作集合。"""

    CREATE = 'create'
    UPDATE = 'update'
    START = 'start'
    COMPLETE = 'complete'
    BLOCK = 'block'
    CANCEL = 'cancel'


class WorkflowRunStatus(StrEnum):
    """工作流运行状态。"""

    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


@dataclass(frozen=True)
class WorkflowStepSpec:
    """单个工作流步骤定义。"""

    action: WorkflowAction
    task_id: str
    title: str | None = None
    description: str | None = None
    dependencies: tuple[str, ...] | None = None
    reason: str | None = None

    def to_dict(self) -> JSONDict:
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
        data = dict(payload or {})
        action = WorkflowAction(str(data.get('action', '')).strip())
        task_id = _normalize_identifier(data.get('task_id', data.get('taskId', '')), label='task_id')
        title = _normalize_optional_text(data.get('title'))
        description = _normalize_optional_text(data.get('description'))
        dependencies = _normalize_optional_dependencies(data.get('dependencies'))
        reason = _normalize_optional_text(data.get('reason'))

        if action is WorkflowAction.CREATE and not title:
            raise ValueError(f'Workflow create step for {task_id!r} requires title')
        if action is WorkflowAction.BLOCK and not reason:
            raise ValueError(f'Workflow block step for {task_id!r} requires reason')

        return cls(
            action=action,
            task_id=task_id,
            title=title,
            description=description,
            dependencies=dependencies,
            reason=reason,
        )


@dataclass(frozen=True)
class WorkflowManifest:
    """单个工作流 manifest。"""

    workflow_id: str
    title: str
    description: str = ''
    steps: tuple[WorkflowStepSpec, ...] = ()
    source_path: Path | None = None

    def to_dict(self) -> JSONDict:
        return {
            'workflow_id': self.workflow_id,
            'title': self.title,
            'description': self.description,
            'steps': [item.to_dict() for item in self.steps],
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None, *, source_path: Path | None = None) -> 'WorkflowManifest':
        data = dict(payload or {})
        workflow_id = _normalize_identifier(
            data.get('workflow_id', data.get('workflowId', '')),
            label='workflow_id',
        )
        title = str(data.get('title', '')).strip()
        if not title:
            raise ValueError(f'Workflow {workflow_id!r} requires non-empty title')

        steps_raw = data.get('steps', [])
        if not isinstance(steps_raw, list):
            raise ValueError(f'Workflow {workflow_id!r} field "steps" must be a JSON array')

        steps = tuple(
            WorkflowStepSpec.from_dict(item)
            for item in steps_raw
            if isinstance(item, dict)
        )
        if not steps:
            raise ValueError(f'Workflow {workflow_id!r} requires at least one step')

        return cls(
            workflow_id=workflow_id,
            title=title,
            description=str(data.get('description', '')).strip(),
            steps=steps,
            source_path=source_path.resolve() if source_path else None,
        )

    @classmethod
    def from_path(cls, manifest_path: Path) -> 'WorkflowManifest':
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Workflow manifest {manifest_path} must contain a JSON object')
        return cls.from_dict(payload, source_path=manifest_path)


@dataclass(frozen=True)
class WorkflowLoadError:
    """工作流 manifest 加载错误。"""

    workflow_id: str
    error: str
    source_path: Path | None = None


@dataclass(frozen=True)
class WorkflowStepResult:
    """单个工作流步骤执行结果。"""

    step_index: int
    action: WorkflowAction
    task_id: str
    ok: bool
    before_status: str | None = None
    after_status: str | None = None
    message: str = ''
    error: str | None = None

    def to_dict(self) -> JSONDict:
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
        data = dict(payload or {})
        return cls(
            step_index=_as_int(data.get('step_index', data.get('stepIndex')), 0),
            action=WorkflowAction(str(data.get('action', '')).strip()),
            task_id=_normalize_identifier(data.get('task_id', data.get('taskId', '')), label='task_id'),
            ok=bool(data.get('ok', False)),
            before_status=_normalize_optional_text(data.get('before_status', data.get('beforeStatus'))),
            after_status=_normalize_optional_text(data.get('after_status', data.get('afterStatus'))),
            message=str(data.get('message', '')).strip(),
            error=_normalize_optional_text(data.get('error')),
        )


@dataclass(frozen=True)
class WorkflowRunRecord:
    """单次工作流运行记录。"""

    run_id: str
    workflow_id: str
    status: WorkflowRunStatus
    started_at: str
    error_message: str | None = None
    step_results: tuple[WorkflowStepResult, ...] = ()

    def to_dict(self) -> JSONDict:
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
        data = dict(payload or {})
        results_raw = data.get('step_results', data.get('stepResults', []))
        if not isinstance(results_raw, list):
            results_raw = []
        return cls(
            run_id=_normalize_identifier(data.get('run_id', data.get('runId', '')), label='run_id'),
            workflow_id=_normalize_identifier(
                data.get('workflow_id', data.get('workflowId', '')),
                label='workflow_id',
            ),
            status=WorkflowRunStatus(str(data.get('status', '')).strip()),
            started_at=str(data.get('started_at', data.get('startedAt', ''))).strip(),
            error_message=_normalize_optional_text(data.get('error_message', data.get('errorMessage'))),
            step_results=tuple(
                WorkflowStepResult.from_dict(item)
                for item in results_raw
                if isinstance(item, dict)
            ),
        )


@dataclass
class WorkflowRuntime:
    """工作区本地 workflow 运行时。"""

    workspace: Path
    manifests: tuple[WorkflowManifest, ...] = ()
    run_history: tuple[WorkflowRunRecord, ...] = ()
    load_errors: tuple[WorkflowLoadError, ...] = ()

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'WorkflowRuntime':
        resolved_workspace = workspace.resolve()
        manifests: list[WorkflowManifest] = []
        load_errors: list[WorkflowLoadError] = []
        seen_ids: set[str] = set()

        for manifest_path in _discover_manifest_paths(resolved_workspace):
            try:
                manifest = WorkflowManifest.from_path(manifest_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                load_errors.append(
                    WorkflowLoadError(
                        workflow_id=manifest_path.stem,
                        error=str(exc),
                        source_path=manifest_path,
                    )
                )
                continue

            if manifest.workflow_id in seen_ids:
                load_errors.append(
                    WorkflowLoadError(
                        workflow_id=manifest.workflow_id,
                        error=f'Duplicate workflow id: {manifest.workflow_id}',
                        source_path=manifest.source_path,
                    )
                )
                continue
            seen_ids.add(manifest.workflow_id)
            manifests.append(manifest)

        return cls(
            workspace=resolved_workspace,
            manifests=tuple(manifests),
            run_history=_load_run_history(resolved_workspace),
            load_errors=tuple(load_errors),
        )

    def list_workflows(self) -> tuple[WorkflowManifest, ...]:
        return self.manifests

    def get_workflow(self, workflow_id: str) -> WorkflowManifest:
        normalized_id = _normalize_identifier(workflow_id, label='workflow_id')
        for manifest in self.manifests:
            if manifest.workflow_id == normalized_id:
                return manifest
        raise ValueError(f'Unknown workflow: {normalized_id!r}')

    def history(self, workflow_id: str | None = None) -> tuple[WorkflowRunRecord, ...]:
        if workflow_id is None:
            return self.run_history
        normalized_id = _normalize_identifier(workflow_id, label='workflow_id')
        return tuple(item for item in self.run_history if item.workflow_id == normalized_id)

    def run_workflow(self, workflow_id: str) -> WorkflowRunRecord:
        manifest = self.get_workflow(workflow_id)
        task_runtime = TaskRuntime.from_workspace(self.workspace)
        step_results: list[WorkflowStepResult] = []
        error_message: str | None = None

        for index, step in enumerate(manifest.steps, start=1):
            before_status = _get_task_status(task_runtime, step.task_id)
            try:
                task = _execute_workflow_step(task_runtime, step)
            except ValueError as exc:
                error_message = str(exc)
                after_status = _get_task_status(task_runtime, step.task_id)
                step_results.append(
                    WorkflowStepResult(
                        step_index=index,
                        action=step.action,
                        task_id=step.task_id,
                        ok=False,
                        before_status=before_status,
                        after_status=after_status,
                        message=f'Failed to execute {step.action.value} on {step.task_id}.',
                        error=error_message,
                    )
                )
                break

            after_status = task.status.value if isinstance(task, TaskRecord) else _get_task_status(task_runtime, step.task_id)
            step_results.append(
                WorkflowStepResult(
                    step_index=index,
                    action=step.action,
                    task_id=step.task_id,
                    ok=True,
                    before_status=before_status,
                    after_status=after_status,
                    message=f'Executed {step.action.value} on {step.task_id}.',
                )
            )

        run_record = WorkflowRunRecord(
            run_id=uuid4().hex,
            workflow_id=manifest.workflow_id,
            status=(WorkflowRunStatus.FAILED if error_message else WorkflowRunStatus.SUCCEEDED),
            started_at=datetime.now(timezone.utc).isoformat(),
            error_message=error_message,
            step_results=tuple(step_results),
        )
        self._append_run_history(run_record)
        return run_record

    def _append_run_history(self, run_record: WorkflowRunRecord) -> None:
        self.run_history = self.run_history + (run_record,)
        _save_run_history(self.workspace, self.run_history)


def _discover_manifest_paths(workspace: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    single_manifest = workspace / _WORKFLOW_MANIFEST_FILE
    if single_manifest.is_file():
        candidates.append(single_manifest)

    manifest_dir = workspace / _WORKFLOW_MANIFEST_DIR
    if manifest_dir.is_dir():
        candidates.extend(
            path.resolve()
            for path in sorted(manifest_dir.glob('*.json'))
            if path.is_file()
        )
    return tuple(candidates)


def _load_run_history(workspace: Path) -> tuple[WorkflowRunRecord, ...]:
    path = workspace / _WORKFLOW_RUN_HISTORY_FILE
    if not path.is_file():
        return ()

    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'Workflow history file {path} must contain a JSON object')

    runs_raw = payload.get('runs', [])
    if not isinstance(runs_raw, list):
        raise ValueError(f'Workflow history file {path} field "runs" must be a JSON array')

    return tuple(
        WorkflowRunRecord.from_dict(item)
        for item in runs_raw
        if isinstance(item, dict)
    )


def _save_run_history(workspace: Path, run_history: tuple[WorkflowRunRecord, ...]) -> Path:
    path = workspace / _WORKFLOW_RUN_HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                'schema_version': _SCHEMA_VERSION,
                'runs': [item.to_dict() for item in run_history],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    return path


def _execute_workflow_step(task_runtime: TaskRuntime, step: WorkflowStepSpec) -> TaskRecord:
    if step.action is WorkflowAction.CREATE:
        return task_runtime.create_task(
            step.task_id,
            step.title or '',
            description=step.description or '',
            dependencies=step.dependencies or (),
        )
    if step.action is WorkflowAction.UPDATE:
        return task_runtime.update_task(
            step.task_id,
            title=step.title,
            description=step.description,
            dependencies=step.dependencies,
        )
    if step.action is WorkflowAction.START:
        return task_runtime.start_task(step.task_id)
    if step.action is WorkflowAction.COMPLETE:
        return task_runtime.complete_task(step.task_id)
    if step.action is WorkflowAction.BLOCK:
        return task_runtime.block_task(step.task_id, reason=step.reason or '')
    if step.action is WorkflowAction.CANCEL:
        return task_runtime.cancel_task(step.task_id)
    raise ValueError(f'Unsupported workflow action: {step.action.value!r}')


def _get_task_status(task_runtime: TaskRuntime, task_id: str) -> str | None:
    try:
        return task_runtime.get_task(task_id).status.value
    except ValueError:
        return None


def _normalize_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{label} must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{label} must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid {label}: {value!r}')
    return normalized


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_optional_dependencies(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError('dependencies must be a list or tuple of task ids')
    normalized_dependencies: list[str] = []
    for item in value:
        dependency_id = _normalize_identifier(item, label='dependency')
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