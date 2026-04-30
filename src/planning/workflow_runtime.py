"""管理工作流清单发现、执行与历史持久化。

本模块只负责 planning 域内部的工作流装载和执行逻辑。
外部调用方必须通过 PlanningGateway 访问这些能力，而不是直接依赖本文件中的 runtime 实现。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core_contracts.planning_contracts import (
    TaskRecord,
    WorkflowAction,
    WorkflowLoadError,
    WorkflowManifest,
    WorkflowRunRecord,
    WorkflowRunStatus,
    WorkflowStepResult,
    WorkflowStepSpec,
)
from planning.task_runtime import TaskRuntime


_WORKFLOW_MANIFEST_FILE = Path('.claw') / 'workflows.json'
_WORKFLOW_MANIFEST_DIR = Path('.claw') / 'workflows'
_WORKFLOW_RUN_HISTORY_FILE = Path('.claw') / 'workflow_runs.json'
_SCHEMA_VERSION = 1


class WorkflowRuntime:
    """管理工作区本地工作流清单与运行历史。

    核心工作流：
    1. `from_workspace` 发现并加载工作流清单及历史记录。
    2. 通过 `list_workflows`、`get_workflow`、`history` 暴露只读视图。
    3. `run_workflow` 顺序执行步骤并记录运行结果。
    """

    def __init__(
        self,
        workspace: Path,
        *,
        manifests: tuple[WorkflowManifest, ...] = (),
        run_history: tuple[WorkflowRunRecord, ...] = (),
        load_errors: tuple[WorkflowLoadError, ...] = (),
    ) -> None:
        """初始化工作流 runtime。
        Args:
            workspace (Path): 工作区根目录。
            manifests (tuple[WorkflowManifest, ...]): 当前已加载的工作流清单集合。
            run_history (tuple[WorkflowRunRecord, ...]): 当前运行历史集合。
            load_errors (tuple[WorkflowLoadError, ...]): 当前加载错误集合。
        Returns:
            None: 该方法仅负责保存状态。
        Raises:
            ValueError: 当工作区路径不存在时抛出。
        """
        resolved_workspace = workspace.resolve()
        if not resolved_workspace.is_dir():
            raise ValueError(f'Workspace directory does not exist: {resolved_workspace}')
        self.workspace = resolved_workspace  # Path：当前工作流 runtime 绑定的工作区根目录。
        self.manifests = tuple(manifests)  # tuple[WorkflowManifest, ...]：加载成功的工作流清单集合。
        self.run_history = tuple(run_history)  # tuple[WorkflowRunRecord, ...]：工作流运行历史集合。
        self.load_errors = tuple(load_errors)  # tuple[WorkflowLoadError, ...]：工作流加载阶段的错误集合。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'WorkflowRuntime':
        """从工作区加载工作流清单与运行历史。
        Args:
            workspace (Path): 工作区根目录。
        Returns:
            WorkflowRuntime: 初始化完成的工作流 runtime。
        Raises:
            ValueError: 当历史文件结构非法时抛出。
        """
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
                        source_path=str(manifest_path),
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
            resolved_workspace,
            manifests=tuple(manifests),
            run_history=_load_run_history(resolved_workspace),
            load_errors=tuple(load_errors),
        )

    def list_workflows(self) -> tuple[WorkflowManifest, ...]:
        """返回当前已成功加载的全部工作流清单。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[WorkflowManifest, ...]: 工作流清单集合。
        Raises:
            无。
        """
        return self.manifests

    def get_workflow(self, workflow_id: str) -> WorkflowManifest:
        """按工作流 ID 返回单个工作流清单。
        Args:
            workflow_id (str): 目标工作流 ID。
        Returns:
            WorkflowManifest: 对应的工作流清单契约。
        Raises:
            ValueError: 当工作流不存在或 ID 非法时抛出。
        """
        normalized_id = _normalize_identifier(workflow_id, label='workflow_id')
        for manifest in self.manifests:
            if manifest.workflow_id == normalized_id:
                return manifest
        raise ValueError(f'Unknown workflow: {normalized_id!r}')

    def history(self, workflow_id: str | None = None) -> tuple[WorkflowRunRecord, ...]:
        """返回工作流运行历史。
        Args:
            workflow_id (str | None): 可选工作流 ID 过滤条件。
        Returns:
            tuple[WorkflowRunRecord, ...]: 过滤后的运行历史集合。
        Raises:
            ValueError: 当工作流 ID 非法时抛出。
        """
        if workflow_id is None:
            return self.run_history
        normalized_id = _normalize_identifier(workflow_id, label='workflow_id')
        return tuple(record for record in self.run_history if record.workflow_id == normalized_id)

    def run_workflow(self, workflow_id: str) -> WorkflowRunRecord:
        """顺序执行指定工作流，并记录本次运行结果。
        Args:
            workflow_id (str): 目标工作流 ID。
        Returns:
            WorkflowRunRecord: 本次运行生成的结果契约。
        Raises:
            ValueError: 当工作流不存在或步骤执行非法时抛出。
        """
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
            status=WorkflowRunStatus.FAILED if error_message else WorkflowRunStatus.SUCCEEDED,
            started_at=datetime.now(timezone.utc).isoformat(),
            error_message=error_message,
            step_results=tuple(step_results),
        )
        self._append_run_history(run_record)
        return run_record

    def _append_run_history(self, run_record: WorkflowRunRecord) -> None:
        """把新的运行记录追加到历史并写回磁盘。
        Args:
            run_record (WorkflowRunRecord): 待追加的运行记录。
        Returns:
            None: 该方法原地更新并落盘保存。
        Raises:
            OSError: 当文件写入失败时抛出。
        """
        self.run_history = self.run_history + (run_record,)
        _save_run_history(self.workspace, self.run_history)


def _discover_manifest_paths(workspace: Path) -> tuple[Path, ...]:
    """发现工作区中的候选工作流清单路径。
    Args:
        workspace (Path): 工作区根目录。
    Returns:
        tuple[Path, ...]: 按稳定顺序排列的清单文件路径集合。
    Raises:
        无。
    """
    candidates: list[Path] = []
    single_manifest = workspace / _WORKFLOW_MANIFEST_FILE
    if single_manifest.is_file():
        candidates.append(single_manifest)
    manifest_dir = workspace / _WORKFLOW_MANIFEST_DIR
    if manifest_dir.is_dir():
        candidates.extend(path.resolve() for path in sorted(manifest_dir.glob('*.json')) if path.is_file())
    return tuple(candidates)


def _load_run_history(workspace: Path) -> tuple[WorkflowRunRecord, ...]:
    """从工作区加载工作流运行历史。
    Args:
        workspace (Path): 工作区根目录。
    Returns:
        tuple[WorkflowRunRecord, ...]: 恢复后的运行历史集合。
    Raises:
        ValueError: 当历史文件结构非法时抛出。
    """
    path = workspace / _WORKFLOW_RUN_HISTORY_FILE
    if not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'Workflow history file {path} must contain a JSON object')
    runs_raw = payload.get('runs', [])
    if not isinstance(runs_raw, list):
        raise ValueError(f'Workflow history file {path} field "runs" must be a JSON array')
    return tuple(WorkflowRunRecord.from_dict(item) for item in runs_raw if isinstance(item, dict))


def _save_run_history(workspace: Path, run_history: tuple[WorkflowRunRecord, ...]) -> None:
    """把工作流运行历史写回工作区文件。
    Args:
        workspace (Path): 工作区根目录。
        run_history (tuple[WorkflowRunRecord, ...]): 待持久化的运行历史集合。
    Returns:
        None: 该方法原地落盘保存。
    Raises:
        OSError: 当文件写入失败时抛出。
    """
    path = workspace / _WORKFLOW_RUN_HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                'schema_version': _SCHEMA_VERSION,
                'runs': [record.to_dict() for record in run_history],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )


def _execute_workflow_step(task_runtime: TaskRuntime, step: WorkflowStepSpec) -> TaskRecord:
    """把单个工作流步骤映射为对应的任务 runtime 调用。
    Args:
        task_runtime (TaskRuntime): 当前任务 runtime。
        step (WorkflowStepSpec): 待执行的工作流步骤。
    Returns:
        TaskRecord: 步骤执行后的任务契约。
    Raises:
        ValueError: 当步骤动作不受支持或输入非法时抛出。
    """
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
    """安全读取任务当前状态值。
    Args:
        task_runtime (TaskRuntime): 当前任务 runtime。
        task_id (str): 目标任务 ID。
    Returns:
        str | None: 任务存在时返回状态值，否则返回 None。
    Raises:
        无。
    """
    try:
        return task_runtime.get_task(task_id).status.value
    except ValueError:
        return None


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

