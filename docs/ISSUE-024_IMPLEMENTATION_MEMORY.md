# ISSUE-024 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/orchestration/agent_manager.py` | 新建 | 实现 child agent record、group、dependency batch 与 stop_reason 汇总 |
| `src/orchestration/local_agent.py` | 修改 | 注册 delegate_agent 工具，并把 child agent 编排接入主循环 |
| `test/orchestration/test_agent_manager.py` | 新建 | 覆盖依赖批处理、unknown/cycle 校验与 group summary |
| `test/orchestration/test_local_agent.py` | 修改 | 覆盖成功委托、依赖失败跳过与 delegated task limit 终止 |
| `README.md` | 修改 | 补充 delegate_agent / AgentManager 使用语义与预算限制说明 |
| `docs/Architecture.md` | 修改 | 把 AgentManager 写入 orchestration 边界图 |
| `docs/FINAL_ARCHITECTURE_PLAN.md` | 修改 | 在 ISSUE-024 下记录已落地设计决策 |

## 关键设计决策

### 1. AgentManager 独立于 TaskRuntime
ISSUE-024 的目标是子代理编排，而不是把 child session 变成持久任务状态机。因此当前实现新增 `src/orchestration/agent_manager.py`，专门承载 parent/child lineage、group、batch 和 stop_reason 汇总，不复用或污染 `planning/task_runtime.py`。

### 2. delegate_agent 是内置 orchestration 工具，但执行走主循环专用分支
为了让模型继续通过统一 tools 协议触发子代理，当前版本把 `delegate_agent` 注册进 LocalAgent 的 tool registry；但真正执行时并不完全走 `LocalToolService.execute()`，而是在 LocalAgent 工具循环里走专用分支。这样可以：

- 共享既有 tool pipeline、transcript 和 tool_result 结构
- 额外写入 child/group runtime events
- 对 `max_delegated_tasks` 给出专门的 `delegated_task_limit` stop_reason

### 3. 依赖批处理采用“拓扑分 batch + batch 内串行”
本期不实现并发 child agent。当前策略是：

- 先按依赖关系生成稳定 batch
- batch 内按输入顺序串行执行
- 上游失败时，下游记录为 `dependency_skipped`

这样既满足“依赖批处理”的需求，又把并发、资源竞争和跨会话协同留给后续 issue。

### 4. child agent 共享 manager，但各自拥有独立 session
每个 child 都使用新的 `LocalAgent` 实例执行，但共享：

- 同一个 `OpenAIClient`
- 同一个 `AgentRuntimeConfig`
- 同一个 `AgentSessionStore`
- 同一个 `AgentManager`

这样 lineage 与 stop_reason 统计可以天然汇聚到同一棵树上，同时 child session 仍各自落盘，满足后续审计与 resume 扩展。

### 5. delegated task limit 在 delegate_agent 前置检查
虽然 `BudgetConfig` 已有 `max_delegated_tasks` 字段，但当前实现没有把它塞进 ISSUE-009 的 `BudgetGuard` 五维通用闸门，而是在 delegate_agent 执行前做专门检查。超限时：

- 当前 tool result 仍会回填 transcript
- 写入 `delegate_group_blocked` 事件
- 父代理 stop_reason 收敛为 `delegated_task_limit`

这样可以保留完整上下文与诊断信息，而不是静默吞掉委托请求。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/orchestration/test_agent_manager.py` | batch plan（2 个） | 依赖批处理保持拓扑顺序，unknown/cycle 依赖会被拒绝 |
| `test/orchestration/test_agent_manager.py` | group summary（1 个） | stop_reason、resume 来源与 dependency skip 统计正确 |
| `test/orchestration/test_local_agent.py` | delegate success（1 个） | 父代理可委托两个 child，并在 tool metadata 中收到 group summary |
| `test/orchestration/test_local_agent.py` | dependency skip（1 个） | 上游 child backend_error 后，下游依赖 child 被标记为 `dependency_skipped` |
| `test/orchestration/test_local_agent.py` | delegated task budget（1 个） | max_delegated_tasks 超限时，父代理在本轮直接以 `delegated_task_limit` 停止 |

## 回归结果

定向验证：

- `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_agent_manager.py" -v` → 3/3 OK
- `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_local_agent.py" -v` → 35/35 OK