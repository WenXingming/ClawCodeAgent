"""管理工作区本地计划的存储、渲染与任务同步。

本模块负责维护 `.claw/plan.json` 中的计划步骤状态，并在需要时把计划步骤投影为任务记录，同步到 `TaskRuntime`。它聚焦于计划视图本身，不负责执行模型或工具调用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from core_contracts.protocol import JSONDict
from planning.task_runtime import TaskRecord, TaskRuntime, TaskStatus


_PLAN_STATE_FILE = Path('.claw') / 'plan.json'
_SCHEMA_VERSION = 1


class PlanStepStatus(StrEnum):
    """计划步骤的稳定状态集合。"""

    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'


@dataclass(frozen=True)
class PlanStep:
    """表示单个计划步骤的稳定记录。

    该对象描述计划中的一个步骤，包括标题、描述、依赖关系与当前状态，可在内存与 JSON 持久化格式之间稳定转换。
    """

    step_id: str  # str：计划步骤的稳定唯一标识。
    title: str  # str：计划步骤的展示标题。
    description: str = ''  # str：计划步骤的补充说明。
    dependencies: tuple[str, ...] = ()  # tuple[str, ...]：当前步骤依赖的前置步骤 ID 列表。
    status: PlanStepStatus = PlanStepStatus.PENDING  # PlanStepStatus：当前步骤状态。

    def to_dict(self) -> JSONDict:
        """把计划步骤转换成可持久化的 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前计划步骤的可序列化字典表示。
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
        """从 JSON 字典恢复单个计划步骤。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
        Returns:
            PlanStep: 恢复后的计划步骤对象。
        Raises:
            ValueError: 当步骤 ID、标题、依赖或状态非法时抛出。
        """
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
    """管理工作区本地计划状态的运行时对象。

    典型工作流如下：
    1. 调用 `from_workspace()` 从 `.claw/plan.json` 加载计划步骤。
    2. 通过 `update_plan()` 或 `clear_plan()` 更新计划内容。
    3. 需要与任务视图保持一致时调用 `sync_tasks()` 把计划步骤同步到 `TaskRuntime`。
    """

    workspace: Path  # Path：当前计划运行时所属的工作区根目录。
    steps_by_id: dict[str, PlanStep] = field(default_factory=dict)  # dict[str, PlanStep]：按步骤 ID 建立的步骤索引。
    step_order: tuple[str, ...] = ()  # tuple[str, ...]：计划步骤的稳定展示顺序。
    schema_version: int = _SCHEMA_VERSION  # int：当前计划状态文件使用的 schema 版本。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'PlanRuntime':
        """从工作区加载计划运行时状态。

        Args:
            workspace (Path): 工作区根目录。

        Returns:
            PlanRuntime: 解析并校验后的计划运行时对象。
        Raises:
            ValueError: 当计划文件结构非法、字段类型错误或存在重复步骤 ID 时抛出。
        """
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
        """把当前计划状态保存到工作区文件。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            Path: 实际写入的计划状态文件路径。
        """
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
        """按稳定顺序返回全部计划步骤。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[PlanStep, ...]: 当前计划步骤列表的只读视图。
        """
        return tuple(
            self.steps_by_id[step_id]
            for step_id in self.step_order
            if step_id in self.steps_by_id
        )

    def get_step(self, step_id: str) -> PlanStep:
        """按步骤 ID 获取单个计划步骤。

        Args:
            step_id (str): 需要读取的计划步骤 ID。
        Returns:
            PlanStep: 找到的计划步骤对象。
        Raises:
            ValueError: 当步骤不存在或步骤 ID 非法时抛出。
        """
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
        """整体更新当前计划步骤集合。

        Args:
            steps (tuple[PlanStep, ...] | list[PlanStep]): 作为新计划内容的步骤集合。
            sync_tasks (bool): 更新后是否立即同步任务视图。
        Returns:
            tuple[PlanStep, ...]: 更新完成后的计划步骤列表。
        Raises:
            ValueError: 当步骤集合包含非法项、重复 ID 或引用未知依赖时抛出。
        """
        normalized_steps = self._normalize_steps(steps)
        self.steps_by_id = {item.step_id: item for item in normalized_steps}
        self.step_order = tuple(item.step_id for item in normalized_steps)
        self.save()
        if sync_tasks:
            return self.sync_tasks()
        return self.list_steps()

    def clear_plan(self, *, sync_tasks: bool = False) -> None:
        """清空当前计划，并按需同步清空任务视图。

        Args:
            sync_tasks (bool): 清空后是否同时把任务运行时也替换为空集合。
        Returns:
            None: 该方法原地更新状态并落盘保存。
        """
        self.steps_by_id = {}
        self.step_order = ()
        self.save()
        if sync_tasks:
            TaskRuntime.from_workspace(self.workspace).replace_tasks(())

    def sync_tasks(self) -> tuple[PlanStep, ...]:
        """把当前计划步骤同步映射为任务记录。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[PlanStep, ...]: 根据同步结果回写状态后的计划步骤列表。
        """
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
        """把当前计划渲染为便于终端显示的纯文本。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            str: 当前计划的文本表示；无步骤时返回 `(none)` 视图。
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
            tuple[PlanStep, ...]: 经过校验、去歧义并继承旧状态后的步骤元组。
        Raises:
            ValueError: 当输入中存在非法项、重复步骤 ID 或引用未知依赖时抛出。
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

        self._validate_dependencies(tuple(normalized_steps))
        return tuple(normalized_steps)

    @staticmethod
    def _validate_dependencies(steps: tuple[PlanStep, ...]) -> None:
        """校验计划步骤依赖是否都指向已知步骤。

        Args:
            steps (tuple[PlanStep, ...]): 需要校验依赖关系的步骤集合。
        Returns:
            None: 校验通过时不返回值。
        Raises:
            ValueError: 当任一步骤引用了不存在的依赖步骤时抛出。
        """
        known_step_ids = {item.step_id for item in steps}
        for step in steps:
            missing_dependencies = [item for item in step.dependencies if item not in known_step_ids]
            if missing_dependencies:
                raise ValueError(
                    f'Plan step {step.step_id!r} references unknown dependencies: {", ".join(missing_dependencies)}'
                )

    @staticmethod
    def _step_to_task_record(step: PlanStep, existing_task: TaskRecord | None) -> TaskRecord:
        """把计划步骤投影为任务记录。

        Args:
            step (PlanStep): 需要映射的计划步骤。
            existing_task (TaskRecord | None): 同 ID 的现有任务记录；存在时用于保留状态信息。
        Returns:
            TaskRecord: 由计划步骤映射得到的任务记录。
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


def _normalize_step_id(value: object) -> str:
    """规范化并校验步骤 ID。

    Args:
        value (object): 待校验的原始步骤 ID。
    Returns:
        str: 去除首尾空白后的合法步骤 ID。
    Raises:
        ValueError: 当步骤 ID 不是字符串、为空或包含非法路径成分时抛出。
    """
    if not isinstance(value, str):
        raise ValueError('step_id must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError('step_id must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid step_id: {value!r}')
    return normalized


def _normalize_dependencies(value: object, *, step_id: str) -> tuple[str, ...]:
    """规范化计划步骤依赖列表。

    Args:
        value (object): 待校验的依赖列表原始值。
        step_id (str): 当前步骤 ID，用于阻止步骤依赖自身。
    Returns:
        tuple[str, ...]: 去重并规范化后的依赖步骤 ID 元组。
    Raises:
        ValueError: 当依赖列表类型非法、依赖 ID 非法或步骤依赖自身时抛出。
    """
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