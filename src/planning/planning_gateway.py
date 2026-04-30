"""planning 领域统一网关。

本模块定义 planning 域的唯一跨域入口 `PlanningGateway`。
外部调用方只能通过该网关访问计划、任务和工作流能力，并使用 core_contracts 中的稳定契约对象交互。
"""

from __future__ import annotations

from pathlib import Path

from core_contracts.planning_contracts import (
    PlanStep,
    TaskRecord,
    WorkflowLoadError,
    WorkflowManifest,
    WorkflowRunRecord,
)
from planning.plan_runtime import PlanRuntime
from planning.task_runtime import TaskRuntime
from planning.workflow_runtime import WorkflowRuntime


class PlanningGateway:
    """统一收口计划、任务与工作流能力。

    核心工作流：
    1. 使用 `from_workspace` 绑定工作区并加载内部 runtime。
    2. 通过计划 API 维护 `.claw/plan.json`。
    3. 通过任务 API 驱动 `.claw/tasks.json`。
    4. 通过工作流 API 发现、执行并追踪 `.claw/workflows` 与历史记录。
    """

    def __init__(self, workspace_root: str) -> None:
        """初始化 planning 网关。
        Args:
            workspace_root (str): 工作区根目录字符串。
        Returns:
            None: 该方法仅负责初始化内部运行时。
        Raises:
            ValueError: 当工作区路径不存在时抛出。
        """
        resolved_workspace = Path(workspace_root).resolve()
        if not resolved_workspace.is_dir():
            raise ValueError(f'Workspace directory does not exist: {resolved_workspace}')
        self._workspace = resolved_workspace  # Path：当前网关绑定的工作区根目录。
        self._plan_runtime = PlanRuntime.from_workspace(resolved_workspace)  # PlanRuntime：计划状态运行时快照。
        self._task_runtime = TaskRuntime.from_workspace(resolved_workspace)  # TaskRuntime：任务状态运行时快照。
        self._workflow_runtime = WorkflowRuntime.from_workspace(resolved_workspace)  # WorkflowRuntime：工作流清单与历史快照。

    @classmethod
    def from_workspace(cls, workspace_root: str) -> 'PlanningGateway':
        """从工作区构建 planning 网关。
        Args:
            workspace_root (str): 工作区根目录字符串。
        Returns:
            PlanningGateway: 初始化完成的 planning 网关。
        Raises:
            ValueError: 当工作区路径不存在时抛出。
        """
        return cls(workspace_root)

    def list_plan_steps(self) -> tuple[PlanStep, ...]:
        """按稳定顺序返回全部计划步骤。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[PlanStep, ...]: 当前计划步骤集合。
        Raises:
            无。
        """
        return self._plan_runtime.list_steps()

    def get_plan_step(self, step_id: str) -> PlanStep:
        """按步骤 ID 返回单个计划步骤。
        Args:
            step_id (str): 目标步骤 ID。
        Returns:
            PlanStep: 对应的计划步骤契约。
        Raises:
            ValueError: 当步骤不存在或 ID 非法时抛出。
        """
        return self._plan_runtime.get_step(step_id)

    def update_plan(self, steps: tuple[PlanStep, ...] | list[PlanStep], *, sync_tasks: bool = False) -> tuple[PlanStep, ...]:
        """整体替换当前计划步骤集合。
        Args:
            steps (tuple[PlanStep, ...] | list[PlanStep]): 新的计划步骤集合。
            sync_tasks (bool): 更新后是否同步任务视图。
        Returns:
            tuple[PlanStep, ...]: 更新后的计划步骤集合。
        Raises:
            ValueError: 当步骤集合非法时抛出。
        """
        updated_steps = self._plan_runtime.update_plan(steps, sync_tasks=sync_tasks)
        self._reload_plan_runtime()
        if sync_tasks:
            self._reload_task_runtime()
        return updated_steps

    def clear_plan(self, *, sync_tasks: bool = False) -> None:
        """清空当前计划。
        Args:
            sync_tasks (bool): 是否同时清空任务视图。
        Returns:
            None: 该方法原地更新并持久化状态。
        Raises:
            无。
        """
        self._plan_runtime.clear_plan(sync_tasks=sync_tasks)
        self._reload_plan_runtime()
        if sync_tasks:
            self._reload_task_runtime()

    def sync_tasks_from_plan(self) -> tuple[PlanStep, ...]:
        """把计划投影到任务并回写计划状态。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[PlanStep, ...]: 同步后的计划步骤集合。
        Raises:
            ValueError: 当底层计划或任务状态非法时抛出。
        """
        synced_steps = self._plan_runtime.sync_tasks()
        self._reload_plan_runtime()
        self._reload_task_runtime()
        return synced_steps

    def render_plan(self) -> str:
        """渲染当前计划的终端文本视图。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            str: 当前计划的纯文本表示。
        Raises:
            无。
        """
        return self._plan_runtime.render_plan()

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        """按稳定顺序返回全部任务记录。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[TaskRecord, ...]: 当前任务集合。
        Raises:
            无。
        """
        return self._task_runtime.list_tasks()

    def next_tasks(self) -> tuple[TaskRecord, ...]:
        """返回当前可执行的待处理任务集合。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[TaskRecord, ...]: 当前可执行任务集合。
        Raises:
            无。
        """
        return self._task_runtime.next_tasks()

    def get_task(self, task_id: str) -> TaskRecord:
        """按任务 ID 返回单个任务记录。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 对应的任务契约。
        Raises:
            ValueError: 当任务不存在或 ID 非法时抛出。
        """
        return self._task_runtime.get_task(task_id)

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
        created_task = self._task_runtime.create_task(
            task_id,
            title,
            description=description,
            dependencies=dependencies,
        )
        self._reload_task_runtime()
        return created_task

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        dependencies: tuple[str, ...] | list[str] | None = None,
    ) -> TaskRecord:
        """更新任务的基础字段。
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
        updated_task = self._task_runtime.update_task(
            task_id,
            title=title,
            description=description,
            dependencies=dependencies,
        )
        self._reload_task_runtime()
        return updated_task

    def start_task(self, task_id: str) -> TaskRecord:
        """把任务切换为进行中。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 更新后的任务契约。
        Raises:
            ValueError: 当任务状态不允许启动时抛出。
        """
        started_task = self._task_runtime.start_task(task_id)
        self._reload_task_runtime()
        return started_task

    def complete_task(self, task_id: str) -> TaskRecord:
        """把任务标记为已完成。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 更新后的任务契约。
        Raises:
            ValueError: 当任务状态不允许完成时抛出。
        """
        completed_task = self._task_runtime.complete_task(task_id)
        self._reload_task_runtime()
        return completed_task

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
        blocked_task = self._task_runtime.block_task(task_id, reason=reason)
        self._reload_task_runtime()
        return blocked_task

    def cancel_task(self, task_id: str) -> TaskRecord:
        """把任务标记为已取消。
        Args:
            task_id (str): 目标任务 ID。
        Returns:
            TaskRecord: 更新后的任务契约。
        Raises:
            ValueError: 当任务状态不允许取消时抛出。
        """
        cancelled_task = self._task_runtime.cancel_task(task_id)
        self._reload_task_runtime()
        return cancelled_task

    def list_workflows(self) -> tuple[WorkflowManifest, ...]:
        """返回当前已加载的全部工作流。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[WorkflowManifest, ...]: 工作流清单集合。
        Raises:
            无。
        """
        return self._workflow_runtime.list_workflows()

    def list_workflow_load_errors(self) -> tuple[WorkflowLoadError, ...]:
        """返回工作流加载阶段收集到的错误集合。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[WorkflowLoadError, ...]: 工作流加载错误集合。
        Raises:
            无。
        """
        return self._workflow_runtime.load_errors

    def get_workflow(self, workflow_id: str) -> WorkflowManifest:
        """按工作流 ID 返回单个工作流清单。
        Args:
            workflow_id (str): 目标工作流 ID。
        Returns:
            WorkflowManifest: 对应的工作流清单契约。
        Raises:
            ValueError: 当工作流不存在或 ID 非法时抛出。
        """
        return self._workflow_runtime.get_workflow(workflow_id)

    def workflow_history(self, workflow_id: str | None = None) -> tuple[WorkflowRunRecord, ...]:
        """返回工作流运行历史。
        Args:
            workflow_id (str | None): 可选工作流 ID 过滤条件。
        Returns:
            tuple[WorkflowRunRecord, ...]: 过滤后的运行历史集合。
        Raises:
            ValueError: 当工作流 ID 非法时抛出。
        """
        return self._workflow_runtime.history(workflow_id)

    def run_workflow(self, workflow_id: str) -> WorkflowRunRecord:
        """顺序执行指定工作流。
        Args:
            workflow_id (str): 目标工作流 ID。
        Returns:
            WorkflowRunRecord: 本次运行生成的结果契约。
        Raises:
            ValueError: 当工作流不存在或某个步骤执行非法时抛出。
        """
        run_record = self._workflow_runtime.run_workflow(workflow_id)
        self._reload_task_runtime()
        self._reload_workflow_runtime()
        return run_record

    def _reload_plan_runtime(self) -> None:
        """重新加载计划 runtime 快照。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            None: 该方法原地刷新内部状态。
        Raises:
            ValueError: 当计划文件结构非法时抛出。
        """
        self._plan_runtime = PlanRuntime.from_workspace(self._workspace)

    def _reload_task_runtime(self) -> None:
        """重新加载任务 runtime 快照。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            None: 该方法原地刷新内部状态。
        Raises:
            ValueError: 当任务文件结构非法时抛出。
        """
        self._task_runtime = TaskRuntime.from_workspace(self._workspace)

    def _reload_workflow_runtime(self) -> None:
        """重新加载工作流 runtime 快照。
        Args:
            None: 该方法不接收额外参数。
        Returns:
            None: 该方法原地刷新内部状态。
        Raises:
            ValueError: 当工作流文件结构非法时抛出。
        """
        self._workflow_runtime = WorkflowRuntime.from_workspace(self._workspace)
