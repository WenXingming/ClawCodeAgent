"""管理工作区计划状态与计划到任务的投影。

本模块只负责 planning 域内部的计划持久化、状态校验和计划到任务的同步映射。
外部调用方必须通过 PlanningGateway 访问这些能力，而不是直接依赖本文件中的 runtime 实现。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from core_contracts.planning import PlanStep, PlanStepStatus, TaskRecord, TaskStatus
from planning.task_runtime import TaskRuntime


_PLAN_STATE_FILE = Path('.claw') / 'plan.json'
_SCHEMA_VERSION = 1


class PlanRuntime:
    """管理工作区本地计划状态。

    核心工作流：
    1. `from_workspace` 读取 `.claw/plan.json` 并恢复步骤顺序。
    2. `update_plan` 或 `clear_plan` 更新计划状态并落盘。
    3. `sync_tasks` 把计划投影到任务视图，并使用任务状态回写步骤状态。
    """

    def __init__(
        self,
        workspace: Path,
        *,
        steps_by_id: dict[str, PlanStep] | None = None,
        step_order: tuple[str, ...] = (),
        schema_version: int = _SCHEMA_VERSION,
    ) -> None:
        """初始化计划 runtime。
        Args:
            workspace (Path): 工作区根目录。
            steps_by_id (dict[str, PlanStep] | None): 当前步骤索引。
            step_order (tuple[str, ...]): 当前步骤顺序。
            schema_version (int): 当前状态文件版本。
        Returns:
            None: 该方法仅负责保存状态。
        Raises:
            ValueError: 当工作区路径不存在时抛出。
        """
        resolved_workspace = workspace.resolve()
        if not resolved_workspace.is_dir():
            raise ValueError(f'Workspace directory does not exist: {resolved_workspace}')
        self.workspace = resolved_workspace  # Path：当前计划 runtime 绑定的工作区根目录。
        self.steps_by_id = dict(steps_by_id or {})  # dict[str, PlanStep]：按步骤 ID 建立的步骤索引。
        self.step_order = tuple(step_order)  # tuple[str, ...]：计划步骤的稳定展示顺序。
        self.schema_version = schema_version  # int：当前计划状态文件版本号。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'PlanRuntime':
        """从工作区加载计划状态。
        Args:
            workspace (Path): 工作区根目录。
        Returns:
            PlanRuntime: 初始化完成的计划 runtime。
        Raises:
            ValueError: 当计划文件结构非法时抛出。
        """
        resolved_workspace = workspace.resolve()
        path = resolved_workspace / _PLAN_STATE_FILE
        if not path.is_file():
            return cls(resolved_workspace)
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
            resolved_workspace,
            steps_by_id=steps_by_id,
            step_order=tuple(step_order),
            schema_version=_as_int(payload.get('schema_version'), _SCHEMA_VERSION),
        )
        runtime._validate_dependencies(runtime.list_steps())
        return runtime

    def save(self) -> None:
        """把当前计划状态写回工作区文件。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            None: 该方法原地落盘保存状态。
        Raises:
            OSError: 当文件写入失败时抛出。
        """
        path = self.workspace / _PLAN_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    'schema_version': self.schema_version,
                    'steps': [step.to_dict() for step in self.list_steps()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

    def list_steps(self) -> tuple[PlanStep, ...]:
        """按稳定顺序返回全部计划步骤。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[PlanStep, ...]: 当前计划步骤集合。
        Raises:
            无。
        """
        return tuple(self.steps_by_id[step_id] for step_id in self.step_order if step_id in self.steps_by_id)

    def get_step(self, step_id: str) -> PlanStep:
        """按步骤 ID 返回单个计划步骤。
        Args:
            step_id (str): 目标步骤 ID。
        Returns:
            PlanStep: 对应的计划步骤契约。
        Raises:
            ValueError: 当步骤不存在或 ID 非法时抛出。
        """
        normalized_id = _normalize_identifier(step_id, label='step_id')
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
        """整体替换当前计划步骤集合。
        Args:
            steps (tuple[PlanStep, ...] | list[PlanStep]): 新的计划步骤集合。
            sync_tasks (bool): 更新后是否同步任务视图。
        Returns:
            tuple[PlanStep, ...]: 更新后的计划步骤集合。
        Raises:
            ValueError: 当步骤集合非法时抛出。
        """
        normalized_steps = self._normalize_steps(steps)
        self.steps_by_id = {step.step_id: step for step in normalized_steps}
        self.step_order = tuple(step.step_id for step in normalized_steps)
        self.save()
        if sync_tasks:
            return self.sync_tasks()
        return self.list_steps()

    def clear_plan(self, *, sync_tasks: bool = False) -> None:
        """清空当前计划。
        Args:
            sync_tasks (bool): 是否同时清空任务视图。
        Returns:
            None: 该方法原地更新并持久化状态。
        Raises:
            无。
        """
        self.steps_by_id = {}
        self.step_order = ()
        self.save()
        if sync_tasks:
            TaskRuntime.from_workspace(self.workspace).replace_tasks(())

    def sync_tasks(self) -> tuple[PlanStep, ...]:
        """把当前计划投影到任务视图，并回写计划状态。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[PlanStep, ...]: 同步后的计划步骤集合。
        Raises:
            ValueError: 当任务替换或状态回写失败时抛出。
        """
        task_runtime = TaskRuntime.from_workspace(self.workspace)
        existing_tasks = {task.task_id: task for task in task_runtime.list_tasks()}
        synced_tasks = task_runtime.replace_tasks(
            tuple(self._step_to_task_record(step, existing_tasks.get(step.step_id)) for step in self.list_steps())
        )
        task_index = {task.task_id: task for task in synced_tasks}
        updated_steps: dict[str, PlanStep] = {}
        for step in self.list_steps():
            synced_task = task_index.get(step.step_id)
            if synced_task is None:
                continue
            updated_steps[step.step_id] = replace(step, status=PlanStepStatus(synced_task.status.value))
        self.steps_by_id = updated_steps
        self.step_order = tuple(step.step_id for step in self.list_steps())
        self.save()
        return self.list_steps()

    def render_plan(self) -> str:
        """渲染当前计划的终端文本视图。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            str: 当前计划的纯文本表示。
        Raises:
            无。
        """
        steps = self.list_steps()
        if not steps:
            return 'Plan Steps\n==========\n(none)'
        lines = ['Plan Steps', '==========']
        for index, step in enumerate(steps, start=1):
            lines.append(f'{index}. [{step.status.value}] {step.step_id} - {step.title}')
        return '\n'.join(lines)

    def _normalize_steps(self, steps: tuple[PlanStep, ...] | list[PlanStep]) -> tuple[PlanStep, ...]:
        """规范化新的计划步骤集合并保留已有状态。
        Args:
            steps (tuple[PlanStep, ...] | list[PlanStep]): 待写入的新步骤集合。
        Returns:
            tuple[PlanStep, ...]: 规范化后的步骤元组。
        Raises:
            ValueError: 当步骤集合非法时抛出。
        """
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
        normalized_tuple = tuple(normalized_steps)
        self._validate_dependencies(normalized_tuple)
        return normalized_tuple

    def _validate_dependencies(self, steps: tuple[PlanStep, ...]) -> None:
        """校验计划步骤依赖是否都指向已知步骤。
        Args:
            steps (tuple[PlanStep, ...]): 待校验的步骤集合。
        Returns:
            None: 校验通过时不返回值。
        Raises:
            ValueError: 当存在未知依赖时抛出。
        """
        known_step_ids = {step.step_id for step in steps}
        for step in steps:
            missing_dependencies = [dependency for dependency in step.dependencies if dependency not in known_step_ids]
            if missing_dependencies:
                raise ValueError(
                    f'Plan step {step.step_id!r} references unknown dependencies: {", ".join(missing_dependencies)}'
                )

    def _step_to_task_record(self, step: PlanStep, existing_task: TaskRecord | None) -> TaskRecord:
        """把单个计划步骤映射为任务记录。
        Args:
            step (PlanStep): 需要投影的计划步骤。
            existing_task (TaskRecord | None): 同 ID 的现有任务记录。
        Returns:
            TaskRecord: 投影后的任务记录契约。
        Raises:
            无。
        """
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
