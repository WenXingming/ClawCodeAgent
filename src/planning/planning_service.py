"""计划与任务管理的唯一公开入口。

该模块提供 PlanningService Facade，作为整个计划和任务子系统对外暴露的唯一窗口。
外层模块只依赖 PlanningService，不直接导入 PlanRuntime、TaskRuntime 或它们的内部实现。

内部仍然使用 plan_runtime、task_runtime、workflow_runtime 的具体实现，
但所有复杂性都被封装在 PlanningService 内部。
"""

from __future__ import annotations

from pathlib import Path

from .plan_runtime import PlanRuntime, PlanStep, PlanStepStatus
from .task_runtime import TaskRecord, TaskRuntime, TaskStatus


class PlanningService:
    """计划与任务管理的唯一公开 Facade。

    该类作为计划与任务子系统的公开门面，负责：
    1. 计划状态管理（通过 PlanRuntime）
    2. 任务状态管理（通过 TaskRuntime）
    3. 计划与任务间的同步投影（sync_tasks）

    外层代码只依赖 PlanningService，保持对计划与任务内部实现的完全隔离。
    """

    def __init__(self, workspace: Path) -> None:
        """初始化计划服务。

        Args:
            workspace (Path): 工作区根目录。
        Returns:
            None: 该方法初始化实例，从工作区加载计划与任务状态。
        """
        self._workspace = workspace.resolve()
        self._plan_runtime = PlanRuntime.from_workspace(self._workspace)
        self._task_runtime = TaskRuntime.from_workspace(self._workspace)

    @property
    def plan_runtime(self) -> PlanRuntime:
        """获取内部计划运行时对象。

        注意：该属性仅供内部使用或高级集成。一般情况下应使用 PlanningService 提供的公开方法。

        Returns:
            PlanRuntime: 当前工作区的计划运行时对象。
        """
        return self._plan_runtime

    @property
    def task_runtime(self) -> TaskRuntime:
        """获取内部任务运行时对象。

        注意：该属性仅供内部使用或高级集成。一般情况下应使用 PlanningService 提供的公开方法。

        Returns:
            TaskRuntime: 当前工作区的任务运行时对象。
        """
        return self._task_runtime

    # ========== 计划 API ==========

    def list_plan_steps(self) -> tuple[PlanStep, ...]:
        """按稳定顺序返回全部计划步骤。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[PlanStep, ...]: 当前计划步骤列表的只读视图。
        """
        return self._plan_runtime.list_steps()

    def get_plan_step(self, step_id: str) -> PlanStep:
        """按步骤 ID 获取单个计划步骤。

        Args:
            step_id (str): 需要读取的计划步骤 ID。
        Returns:
            PlanStep: 找到的计划步骤对象。
        Raises:
            ValueError: 当步骤不存在或步骤 ID 非法时抛出。
        """
        return self._plan_runtime.get_step(step_id)

    def update_plan(
        self,
        steps: tuple[PlanStep, ...] | list[PlanStep],
    ) -> None:
        """更新计划步骤。

        Args:
            steps (tuple[PlanStep, ...] | list[PlanStep]): 新的计划步骤集合。
        Returns:
            None: 该方法原地更新计划状态并保存到磁盘。
        Raises:
            ValueError: 当步骤集合包含重复 ID、循环依赖或缺失依赖时抛出。
        """
        self._plan_runtime.update_plan(steps)
        self._plan_runtime.save()

    def clear_plan(self) -> None:
        """清空计划。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            None: 该方法清空计划步骤并保存到磁盘。
        """
        self._plan_runtime.clear_plan()
        self._plan_runtime.save()

    def save_plan(self) -> Path:
        """保存计划到工作区。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            Path: 实际写入的计划状态文件路径。
        """
        return self._plan_runtime.save()

    # ========== 任务 API ==========

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        """按稳定顺序返回全部任务记录。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[TaskRecord, ...]: 当前任务列表的只读视图。
        """
        return self._task_runtime.list_tasks()

    def get_task(self, task_id: str) -> TaskRecord:
        """按任务 ID 获取单个任务记录。

        Args:
            task_id (str): 需要读取的任务唯一标识。
        Returns:
            TaskRecord: 找到的任务记录对象。
        Raises:
            ValueError: 当任务不存在或任务 ID 非法时抛出。
        """
        return self._task_runtime.get_task(task_id)

    def create_task(
        self,
        task_id: str,
        title: str,
        description: str = '',
        dependencies: tuple[str, ...] | list[str] = (),
    ) -> TaskRecord:
        """创建新任务。

        Args:
            task_id (str): 任务的稳定唯一标识。
            title (str): 任务标题。
            description (str): 任务描述（可选）。
            dependencies (tuple[str, ...] | list[str]): 任务的前置依赖列表（可选）。
        Returns:
            TaskRecord: 新创建的任务记录对象。
        Raises:
            ValueError: 当任务 ID 重复或前置依赖不存在时抛出。
        """
        task = self._task_runtime.create_task(
            task_id=task_id,
            title=title,
            description=description,
            dependencies=dependencies,
        )
        self._task_runtime.save()
        return task

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: TaskStatus | None = None,
        blocked_by: tuple[str, ...] | list[str] | None = None,
        manual_block_reason: str | None = None,
    ) -> TaskRecord:
        """更新任务信息。

        Args:
            task_id (str): 需要更新的任务 ID。
            title (str | None): 新的标题（可选）。
            description (str | None): 新的描述（可选）。
            status (TaskStatus | None): 新的状态（可选）。
            blocked_by (tuple[str, ...] | list[str] | None): 新的阻塞依赖列表（可选）。
            manual_block_reason (str | None): 新的阻塞原因（可选）。
        Returns:
            TaskRecord: 更新后的任务记录对象。
        Raises:
            ValueError: 当任务不存在或新数据非法时抛出。
        """
        task = self._task_runtime.update_task(
            task_id=task_id,
            title=title,
            description=description,
            status=status,
            blocked_by=blocked_by,
            manual_block_reason=manual_block_reason,
        )
        self._task_runtime.save()
        return task

    def start_task(self, task_id: str) -> TaskRecord:
        """标记任务为进行中。

        Args:
            task_id (str): 需要启动的任务 ID。
        Returns:
            TaskRecord: 更新后的任务记录对象。
        Raises:
            ValueError: 当任务不存在或状态不允许时抛出。
        """
        task = self._task_runtime.start_task(task_id)
        self._task_runtime.save()
        return task

    def complete_task(self, task_id: str) -> TaskRecord:
        """标记任务为完成。

        Args:
            task_id (str): 需要完成的任务 ID。
        Returns:
            TaskRecord: 更新后的任务记录对象。
        Raises:
            ValueError: 当任务不存在或状态不允许时抛出。
        """
        task = self._task_runtime.complete_task(task_id)
        self._task_runtime.save()
        return task

    def cancel_task(self, task_id: str) -> TaskRecord:
        """标记任务为已取消。

        Args:
            task_id (str): 需要取消的任务 ID。
        Returns:
            TaskRecord: 更新后的任务记录对象。
        Raises:
            ValueError: 当任务不存在或状态不允许时抛出。
        """
        task = self._task_runtime.cancel_task(task_id)
        self._task_runtime.save()
        return task

    def save_tasks(self) -> Path:
        """保存任务到工作区。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            Path: 实际写入的任务状态文件路径。
        """
        return self._task_runtime.save()

    # ========== 同步 API ==========

    def sync_tasks_from_plan(self) -> None:
        """把计划步骤投影为任务，同步到任务运行时。

        该方法遍历当前计划步骤，为每个步骤创建对应任务（如果不存在），
        并更新任务的状态与依赖关系以保持与计划同步。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            None: 该方法原地更新任务运行时并保存到磁盘。
        """
        self._plan_runtime.sync_tasks(self._task_runtime)
        self._task_runtime.save()


# 导出稳定的数据契约，作为 PlanningService 的公开契约
__all__ = [
    'PlanningService',
    'PlanStep',
    'PlanStepStatus',
    'TaskRecord',
    'TaskStatus',
]
