"""管理工作流清单读取、执行与运行历史持久化。

本模块负责从工作区加载工作流定义，按步骤驱动 `TaskRuntime` 执行任务操作，并把每次工作流运行结果保存到历史记录中。它提供的是本地工作流运行时，不负责远程编排或模型调用。
"""

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
    """工作流运行状态集合。"""

    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


@dataclass(frozen=True)
class WorkflowStepSpec:
    """表示单个工作流步骤定义。

    每个步骤描述一次针对 `TaskRuntime` 的具体操作，包括动作类型、目标任务 ID，以及该动作所需的附加参数。
    """

    action: WorkflowAction  # WorkflowAction：当前步骤要执行的动作类型。
    task_id: str  # str：当前步骤操作的目标任务 ID。
    title: str | None = None  # str | None：创建或更新任务时使用的标题。
    description: str | None = None  # str | None：创建或更新任务时使用的描述。
    dependencies: tuple[str, ...] | None = None  # tuple[str, ...] | None：创建或更新任务时写入的依赖列表。
    reason: str | None = None  # str | None：阻塞任务动作所需的原因说明。

    def to_dict(self) -> JSONDict:
        """把工作流步骤定义转换成可持久化的 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前步骤定义的可序列化字典表示。
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
        """从 JSON 字典恢复单个工作流步骤定义。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            WorkflowStepSpec: 恢复后的工作流步骤定义对象。
        Raises:
            ValueError: 当动作类型、任务 ID 或动作所需字段非法时抛出。
        """
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
    """表示单个工作流清单定义。

    该对象承载一个工作流的元数据和步骤列表，是工作流文件在内存中的稳定表示。外部通常通过 `from_path()` 或 `from_dict()` 构建此对象。
    """

    workflow_id: str  # str：工作流的稳定唯一标识。
    title: str  # str：工作流展示标题。
    description: str = ''  # str：工作流补充说明。
    steps: tuple[WorkflowStepSpec, ...] = ()  # tuple[WorkflowStepSpec, ...]：按顺序执行的工作流步骤集合。
    source_path: Path | None = None  # Path | None：当前清单文件的来源路径。

    def to_dict(self) -> JSONDict:
        """把工作流清单转换成可持久化的 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前工作流清单的可序列化字典表示。
        """
        return {
            'workflow_id': self.workflow_id,
            'title': self.title,
            'description': self.description,
            'steps': [item.to_dict() for item in self.steps],
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None, *, source_path: Path | None = None) -> 'WorkflowManifest':
        """从 JSON 字典恢复工作流清单对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
            source_path (Path | None): 当前清单来源的文件路径。
        Returns:
            WorkflowManifest: 恢复后的工作流清单对象。
        Raises:
            ValueError: 当工作流 ID、标题、步骤列表或步骤定义非法时抛出。
        """
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
        """从磁盘文件加载并解析工作流清单。

        Args:
            manifest_path (Path): 待加载的工作流清单文件路径。
        Returns:
            WorkflowManifest: 解析成功后的工作流清单对象。
        Raises:
            ValueError: 当文件内容不是合法的工作流对象时抛出。
            OSError: 当文件读取失败时抛出。
            json.JSONDecodeError: 当文件内容不是合法 JSON 时抛出。
        """
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Workflow manifest {manifest_path} must contain a JSON object')
        return cls.from_dict(payload, source_path=manifest_path)


@dataclass(frozen=True)
class WorkflowLoadError:
    """表示工作流清单加载失败时的错误信息。"""

    workflow_id: str  # str：加载失败的工作流 ID 或文件 stem。
    error: str  # str：对应的错误说明文本。
    source_path: Path | None = None  # Path | None：出错的来源文件路径。


@dataclass(frozen=True)
class WorkflowStepResult:
    """表示单个工作流步骤的执行结果。"""

    step_index: int  # int：当前步骤在工作流中的 1-based 顺序。
    action: WorkflowAction  # WorkflowAction：当前步骤执行的动作类型。
    task_id: str  # str：当前步骤作用的任务 ID。
    ok: bool  # bool：当前步骤是否执行成功。
    before_status: str | None = None  # str | None：执行前任务状态。
    after_status: str | None = None  # str | None：执行后任务状态。
    message: str = ''  # str：面向用户或日志的简短结果描述。
    error: str | None = None  # str | None：执行失败时记录的错误详情。

    def to_dict(self) -> JSONDict:
        """把步骤执行结果转换成可持久化的 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前步骤执行结果的可序列化字典表示。
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
        """从 JSON 字典恢复步骤执行结果对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            WorkflowStepResult: 恢复后的步骤执行结果对象。
        Raises:
            ValueError: 当动作类型或任务 ID 非法时抛出。
        """
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
    """表示一次完整的工作流运行记录。"""

    run_id: str  # str：本次工作流运行的稳定唯一标识。
    workflow_id: str  # str：本次运行对应的工作流 ID。
    status: WorkflowRunStatus  # WorkflowRunStatus：本次运行的最终结果状态。
    started_at: str  # str：本次运行开始时的 UTC ISO 时间戳。
    error_message: str | None = None  # str | None：运行失败时的整体错误说明。
    step_results: tuple[WorkflowStepResult, ...] = ()  # tuple[WorkflowStepResult, ...]：各步骤的执行结果集合。

    def to_dict(self) -> JSONDict:
        """把工作流运行记录转换成可持久化的 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前工作流运行记录的可序列化字典表示。
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
        """从 JSON 字典恢复工作流运行记录对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            WorkflowRunRecord: 恢复后的工作流运行记录对象。
        Raises:
            ValueError: 当运行 ID、工作流 ID 或状态非法时抛出。
        """
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
    """管理工作区本地工作流清单与运行历史的运行时对象。

    典型工作流如下：
    1. 调用 `from_workspace()` 加载工作流清单和历史记录。
    2. 通过 `list_workflows()`、`get_workflow()` 和 `history()` 浏览当前状态。
    3. 调用 `run_workflow()` 顺序执行某个工作流，并由 `_append_run_history()` 负责落盘记录。
    """

    workspace: Path  # Path：当前工作流运行时所属的工作区根目录。
    manifests: tuple[WorkflowManifest, ...] = ()  # tuple[WorkflowManifest, ...]：当前工作区加载成功的工作流清单集合。
    run_history: tuple[WorkflowRunRecord, ...] = ()  # tuple[WorkflowRunRecord, ...]：历史运行记录集合。
    load_errors: tuple[WorkflowLoadError, ...] = ()  # tuple[WorkflowLoadError, ...]：加载清单阶段收集到的错误信息。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'WorkflowRuntime':
        """从工作区加载工作流清单与运行历史。

        Args:
            workspace (Path): 工作区根目录。

        Returns:
            WorkflowRuntime: 初始化后的工作流运行时对象。
        Raises:
            ValueError: 当运行历史文件结构非法时抛出。
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
        """返回当前已成功加载的全部工作流清单。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[WorkflowManifest, ...]: 当前工作流清单集合的只读视图。
        """
        return self.manifests

    def get_workflow(self, workflow_id: str) -> WorkflowManifest:
        """按工作流 ID 获取单个工作流清单。

        Args:
            workflow_id (str): 需要读取的工作流 ID。
        Returns:
            WorkflowManifest: 找到的工作流清单对象。
        Raises:
            ValueError: 当工作流不存在或工作流 ID 非法时抛出。
        """
        normalized_id = _normalize_identifier(workflow_id, label='workflow_id')
        for manifest in self.manifests:
            if manifest.workflow_id == normalized_id:
                return manifest
        raise ValueError(f'Unknown workflow: {normalized_id!r}')

    def history(self, workflow_id: str | None = None) -> tuple[WorkflowRunRecord, ...]:
        """返回工作流运行历史。

        Args:
            workflow_id (str | None): 可选的工作流 ID；传入后仅返回该工作流的历史。
        Returns:
            tuple[WorkflowRunRecord, ...]: 过滤后的工作流运行历史集合。
        Raises:
            ValueError: 当传入的工作流 ID 非法时抛出。
        """
        if workflow_id is None:
            return self.run_history
        normalized_id = _normalize_identifier(workflow_id, label='workflow_id')
        return tuple(item for item in self.run_history if item.workflow_id == normalized_id)

    def run_workflow(self, workflow_id: str) -> WorkflowRunRecord:
        """顺序执行指定工作流，并记录本次运行结果。

        Args:
            workflow_id (str): 需要执行的工作流 ID。
        Returns:
            WorkflowRunRecord: 本次执行生成的运行记录。
        Raises:
            ValueError: 当工作流不存在或某个步骤执行遇到非法输入时按现有逻辑记录失败结果。
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
            status=(WorkflowRunStatus.FAILED if error_message else WorkflowRunStatus.SUCCEEDED),
            started_at=datetime.now(timezone.utc).isoformat(),
            error_message=error_message,
            step_results=tuple(step_results),
        )
        self._append_run_history(run_record)
        return run_record

    def _append_run_history(self, run_record: WorkflowRunRecord) -> None:
        """把新的运行记录追加到历史并写回磁盘。

        Args:
            run_record (WorkflowRunRecord): 需要追加保存的工作流运行记录。
        Returns:
            None: 该方法原地更新运行时并落盘保存。
        """
        self.run_history = self.run_history + (run_record,)
        _save_run_history(self.workspace, self.run_history)


def _discover_manifest_paths(workspace: Path) -> tuple[Path, ...]:
    """发现工作区中所有候选工作流清单文件路径。

    Args:
        workspace (Path): 工作区根目录。
    Returns:
        tuple[Path, ...]: 按稳定顺序返回的工作流清单文件路径元组。
    """
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
    """从工作区加载工作流运行历史。

    Args:
        workspace (Path): 工作区根目录。
    Returns:
        tuple[WorkflowRunRecord, ...]: 恢复后的运行历史记录元组。
    Raises:
        ValueError: 当历史文件结构非法时抛出。
        OSError: 当文件读取失败时抛出。
        json.JSONDecodeError: 当历史文件内容不是合法 JSON 时抛出。
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

    return tuple(
        WorkflowRunRecord.from_dict(item)
        for item in runs_raw
        if isinstance(item, dict)
    )


def _save_run_history(workspace: Path, run_history: tuple[WorkflowRunRecord, ...]) -> Path:
    """把工作流运行历史写回工作区文件。

    Args:
        workspace (Path): 工作区根目录。
        run_history (tuple[WorkflowRunRecord, ...]): 需要持久化的运行历史集合。
    Returns:
        Path: 实际写入的运行历史文件路径。
    """
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
    """把单个工作流步骤映射为对应的任务运行时调用。

    Args:
        task_runtime (TaskRuntime): 当前要被驱动的任务运行时对象。
        step (WorkflowStepSpec): 需要执行的工作流步骤定义。
    Returns:
        TaskRecord: 当前步骤执行后返回的任务记录。
    Raises:
        ValueError: 当步骤动作不受支持或所需参数非法时抛出。
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
    """安全读取指定任务的当前状态值。

    Args:
        task_runtime (TaskRuntime): 当前任务运行时对象。
        task_id (str): 需要读取状态的任务 ID。
    Returns:
        str | None: 任务存在时返回状态值，否则返回 None。
    """
    try:
        return task_runtime.get_task(task_id).status.value
    except ValueError:
        return None


def _normalize_identifier(value: object, *, label: str) -> str:
    """规范化并校验带标签的标识符。

    Args:
        value (object): 待校验的原始标识符。
        label (str): 当前标识符的字段标签，用于构造错误信息。
    Returns:
        str: 去除首尾空白后的合法标识符。
    Raises:
        ValueError: 当标识符不是字符串、为空或包含非法路径成分时抛出。
    """
    if not isinstance(value, str):
        raise ValueError(f'{label} must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{label} must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid {label}: {value!r}')
    return normalized


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


def _normalize_optional_dependencies(value: object) -> tuple[str, ...] | None:
    """规范化可选依赖列表。

    Args:
        value (object): 待校验的依赖列表原始值。
    Returns:
        tuple[str, ...] | None: 去重并规范化后的依赖元组；若未提供则返回 None。
    Raises:
        ValueError: 当依赖列表类型非法或依赖标识符非法时抛出。
    """
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