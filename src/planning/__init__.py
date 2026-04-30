"""planning 领域唯一公开入口。

本包对外只暴露 `PlanningGateway`。
计划、任务和工作流的数据契约统一定义在 `core_contracts.planning_contracts`，
外部调用方不得直接导入 planning 包内部 runtime 文件。
"""

from planning.planning_gateway import PlanningGateway

__all__ = ['PlanningGateway']

