# ISSUE-018 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/planning/plan_runtime.py` | 新建 | 实现 PlanStep、计划存储、渲染与 plan-task 同步 |
| `src/planning/task_runtime.py` | 修改 | 新增 `replace_tasks()`，支持计划同步后的整表替换 |
| `test/planning/test_plan_runtime.py` | 新建 | 覆盖更新同步、依赖映射、状态回写与清空同步 |
| `README.md` | 修改 | 补充工作区 Plan Runtime 的持久化路径、接口与示例 |
| `docs/Architecture.md` | 修改 | 写回 plan runtime 在 runtime 包中的位置与依赖关系 |

## 关键设计决策

### 1. Plan Runtime 保持独立，不直接接入 agent 主循环
ISSUE-018 的目标是“计划更新、清空及与 task 同步”，不是让代理立即消费计划。因此当前实现把 `planning/plan_runtime.py` 设计成与 `agent_runtime.py` 解耦的独立模块，后续再由 control plane 或 workflow issue 接入。

### 2. 计划文件沿用 `.claw/` 约定，单独落到 `.claw/plan.json`
插件、policy、task 都已经使用工作区 `.claw/` 目录；Plan Runtime 保持同一约定，把计划单独存到 `.claw/plan.json`，避免与任务文件混写，也便于后续单独清空、渲染和恢复。

### 3. `PlanStepStatus` 复用 Task Runtime 的五态语义
为减少 plan-task 同步时的语义转换成本，`PlanStepStatus` 采用与 `TaskStatus` 对齐的五个值：

- `pending`
- `in_progress`
- `completed`
- `blocked`
- `cancelled`

这样 `sync_tasks()` 可以直接把任务状态回写到计划步骤，而不需要额外定义映射表或引入“半同步状态”。

### 4. `sync_tasks()` 使用 `step_id -> task_id` 的一一映射，并通过 `replace_tasks()` 保证列表一致
本期 DoD 明确要求“同步后任务列表与计划一致”。因此实现没有对任务做增量 merge，而是由 `PlanRuntime` 生成目标任务快照，再调用 `TaskRuntime.replace_tasks()` 做整表替换：

- 计划中新增的步骤会生成新任务
- 计划中移除的步骤会从任务列表中删除
- 已存在的任务会保留当前执行状态与手工 block 原因

这比在 `PlanRuntime` 内部直接操作任务 JSON 文件更安全，也把任务持久化规则继续集中在 `TaskRuntime` 内部。

### 5. `update_plan(sync_tasks=True)` 与 `clear_plan(sync_tasks=True)` 作为最小闭环入口
为满足“更新同步”和“清空同步”测试用例，当前实现把同步能力挂在两个公开入口上：

- `update_plan(..., sync_tasks=True)`：更新步骤并立即把结构同步到任务列表
- `clear_plan(sync_tasks=True)`：清空计划并一并清空任务列表

这样外层调用方不需要先更新计划、再手工调用多次同步接口才能得到一致状态。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test_plan_runtime` | update sync（1 个） | 计划更新后 `.claw/plan.json` 稳定落盘，任务列表与依赖关系同步生成 |
| `test_plan_runtime` | status sync（1 个） | Task Runtime 状态变化后，`sync_tasks()` 会把状态回写到 `PlanStep.status` |
| `test_plan_runtime` | clear sync（1 个） | 清空计划时可同步清空任务文件与内存状态 |
| `test_plan_runtime` | replace sync（1 个） | 计划移除步骤时，任务列表也会同步移除对应任务 |

## 回归结果

定向验证：

- `python -m unittest discover -s test/planning -p "test_plan_runtime.py" -v` → 4/4 OK