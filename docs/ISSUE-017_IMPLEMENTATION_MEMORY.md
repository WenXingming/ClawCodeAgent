# ISSUE-017 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/runtime/task_runtime.py` | 新建 | 实现任务状态模型、本地 JSON 持久化、依赖阻塞/释放与 next tasks 选择 |
| `test/runtime/test_task_runtime.py` | 新建 | 覆盖状态流转、依赖阻塞释放、update/list、next tasks 与落盘恢复 |
| `README.md` | 修改 | 补充工作区 Task Runtime 的持久化路径、状态集合和程序化示例 |
| `docs/Architecture.md` | 修改 | 把 task runtime 写回 runtime 包视图与阅读路径 |

## 关键设计决策

### 1. Task Runtime 保持独立，不提前耦合 agent 主循环
ISSUE-017 的目标只是实现任务状态机和本地持久化；plan-task 同步在 ISSUE-018，workflow 记录在 ISSUE-019。因此当前实现选择把 `runtime/task_runtime.py` 做成一个独立模块，不修改 `LocalCodingAgent` 或 CLI 面。

### 2. 使用工作区本地 `.claw/tasks.json` 作为唯一持久化入口
插件和 policy 已经都落在 `.claw/` 下面；Task Runtime 沿用同一约定，把任务状态落到 `.claw/tasks.json`。这样路径规则统一，也便于后续 plan runtime 直接同步同一工作区的任务文件。

### 3. 状态模型最小化为五个稳定状态
当前实现定义：

- `pending`
- `in_progress`
- `completed`
- `blocked`
- `cancelled`

其中依赖未满足会自动把任务置为 `blocked`，依赖完成后再自动释放回 `pending`；手动 `block_task()` 则通过 `manual_block_reason` 保持阻塞，不会被依赖释放逻辑误解除。

### 4. 每次变更操作自动持久化
`create/update/start/complete/block/cancel` 在内存变更后都会立刻写回 `.claw/tasks.json`，避免出现“内存状态正确但磁盘状态滞后”的双写语义问题。`from_workspace()` 则负责按 UTF-8 JSON 稳定恢复历史任务状态。

### 5. `next_tasks()` 只返回当前 actionable 的 `pending` 任务
当前 issue 的关键输出不是调度器，而是“下一批可执行任务”的稳定选择。因此 `next_tasks()` 只返回已经解除依赖阻塞、且未被手工 block/cancel 的 `pending` 任务，并保持创建顺序稳定，便于后续 plan/workflow 层直接消费。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test_task_runtime` | transitions（1 个） | 非法完成/重复开始/完成后取消等非法状态迁移被拒绝 |
| `test_task_runtime` | dependency release（1 个） | 依赖任务未完成时自动 blocked，完成后自动释放为 pending |
| `test_task_runtime` | next tasks（1 个） | 手工 blocked 与 cancelled 任务不会出现在 actionable 列表中 |
| `test_task_runtime` | persistence（1 个） | `.claw/tasks.json` 能稳定写回并恢复任务状态 |
| `test_task_runtime` | update/list（1 个） | 更新字段与依赖后状态重算正确，列表顺序稳定 |

## 回归结果

定向验证：

- `python -m unittest discover -s test/runtime -p "test_task_runtime.py" -v` → 5/5 OK