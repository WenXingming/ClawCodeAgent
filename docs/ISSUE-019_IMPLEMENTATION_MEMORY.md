# ISSUE-019 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/planning/workflow_runtime.py` | 新建 | 实现 workflow manifest 发现、顺序执行与运行历史持久化 |
| `test/planning/test_workflow_runtime.py` | 新建 | 覆盖发现、运行成功、运行失败与历史回放 |
| `README.md` | 修改 | 补充工作区 Workflow Runtime 的 manifest 路径、动作集合与示例 |
| `docs/Architecture.md` | 修改 | 把 workflow runtime 写回 runtime 包视图与依赖关系 |

## 关键设计决策

### 1. Workflow Runtime 保持独立，不直接接 agent 主循环
ISSUE-019 的目标是“工作流定义读取、运行和历史记录”，不是马上让 agent 消费 workflow。因此当前实现把 `planning/workflow_runtime.py` 保持为独立模块，后续再由 control plane 或 workflow 相关 issue 接入。

### 2. workflow manifest 定义为一串 Task Runtime 操作序列
ISSUE-019 的唯一前置依赖是 ISSUE-017，因此当前 workflow 直接建立在 Task Runtime 能力之上。manifest 中每个步骤都对应一个任务动作：

- `create`
- `update`
- `start`
- `complete`
- `block`
- `cancel`

这样 workflow 的“运行”就是顺序调用 Task Runtime 状态机，而不是额外发明一套新的执行语义。

### 3. manifest 与 run history 都落在 `.claw/` 目录
当前实现使用：

- `.claw/workflows.json`
- `.claw/workflows/*.json`
- `.claw/workflow_runs.json`

前两者用于发现工作流定义，后者用于持久化运行记录。这样路径约定与 plugin / policy / task / plan 保持一致，也方便工作区内整体自举。

### 4. 失败不抛出到调用方，而是返回 `failed` 的运行记录
为满足“错误可诊断”和“历史可回放”，`run_workflow()` 在步骤失败时不会把异常继续向外抛给上层，而是：

- 记录失败的步骤索引、动作、task_id
- 保存失败前后的 task 状态
- 把错误文本写入 run record 和 step result
- 终止后续步骤执行

这样成功和失败运行都会进入同一个历史流，便于排查和复现实验过程。

### 5. 运行历史以 step-level result 保存，保证可回放
每条 `WorkflowRunRecord` 都会保存：

- `run_id`
- `workflow_id`
- `status`
- `started_at`
- `error_message`
- `step_results`

其中 `step_results` 会记录每一步的 `before_status`、`after_status`、`ok` 和 `error`。这样即便 workflow 失败，也能从历史文件回看它在哪一步、以什么状态失败。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/planning/test_workflow_runtime.py` | discovery（1 个） | workflow manifest 可被发现、列出和读取 |
| `test/planning/test_workflow_runtime.py` | run success（1 个） | 顺序执行 task 动作成功，任务状态更新并写入历史文件 |
| `test/planning/test_workflow_runtime.py` | run failure（1 个） | 非法状态迁移会被记录为 failed run，错误信息可诊断且历史可重载 |

## 回归结果

定向验证：

- `python -m unittest discover -s test/planning -p "test_workflow_runtime.py" -v` → 3/3 OK