# REFAC Step 07 Implementation Memory

## Step Goal

根据 [docs/architecture/GLOBAL_REFACTOR_PLAN.md](/docs/architecture/GLOBAL_REFACTOR_PLAN.md) 的 Step 7，本步目标是拆毁 `local_agent.py` 巨石，重建 `agent/` 核心子系统，收口为唯一 `Agent` 门面，并删除旧 `orchestration.local_agent`、`orchestration.agent_manager`、`budget.budget_guard` 三条历史边界。

## Completed Work

1. 新建 [src/agent/agent.py](src/agent/agent.py)，引入 `Agent` 作为运行时唯一 facade，仅暴露 `run()` 与 `resume()`。
2. 新建 [src/agent/prompt_processor.py](src/agent/prompt_processor.py)，收口 slash 分流与 prompt 预处理。
3. 新建 [src/agent/turn_coordinator.py](src/agent/turn_coordinator.py)，承接主循环编排。
4. 新建 [src/agent/run_limits.py](src/agent/run_limits.py)，吸收并替换旧 `BudgetGuard`。
5. 新建 [src/agent/delegation_service.py](src/agent/delegation_service.py)，吸收并替换旧 `AgentManager`。
6. 新建 [src/agent/result_factory.py](src/agent/result_factory.py)，收口 session 快照落盘与 `AgentRunResult` 构建。
7. 新建 [src/agent/__init__.py](src/agent/__init__.py)，包级只导出 `Agent`。
8. 更新 [src/context/context_manager.py](src/context/context_manager.py)，预检 guard 类型切到 `RunLimits`。
9. 更新外层依赖入口：
   - [src/main.py](src/main.py)
   - [src/interaction/command_line_interaction.py](src/interaction/command_line_interaction.py)
   - [src/orchestration/query_engine.py](src/orchestration/query_engine.py)
10. 删除旧生产文件：
   - [src/orchestration/local_agent.py](src/orchestration/local_agent.py)
   - [src/orchestration/agent_manager.py](src/orchestration/agent_manager.py)
   - [src/budget/budget_guard.py](src/budget/budget_guard.py)
11. 迁移并更新 agent 相关测试：
   - [test/agent/test_agent.py](test/agent/test_agent.py)
   - [test/agent/test_delegation_service.py](test/agent/test_delegation_service.py)
   - [test/agent/test_run_limits.py](test/agent/test_run_limits.py)
   - [test/agent/test_run_limits_context_manager.py](test/agent/test_run_limits_context_manager.py)
   - [test/orchestration/test_query_engine.py](test/orchestration/test_query_engine.py)（切到 `Agent` 导入）
   - [test/test_main.py](test/test_main.py) 与 [test/test_main_chat.py](test/test_main_chat.py)（patch 目标切到 `main.Agent`）
12. 更新活文档：
   - [docs/architecture/Architecture.md](/docs/architecture/Architecture.md)
   - [docs/release/TEST_MATRIX.md](/docs/release/TEST_MATRIX.md)

## Production Refactor Outcome

### 1. `Agent` 成为唯一运行门面

当前控制面与运行门面都通过 `Agent` 调用 run/resume：

1. `main.py` 默认注入 `Agent`。
2. CLI 的 `agent_cls` 类型约束切到 `Agent`。
3. QueryEngine 的 runtime facade 从 `LocalAgent` 切到 `Agent`。

### 2. 主循环职责被拆分为明确协作者

1. `TurnCoordinator`：主循环推进与工具回填。
2. `PromptProcessor`：slash 分流与 prompt 预处理。
3. `RunLimits`：预算闸门。
4. `DelegationService`：delegate lineage/group/batch 汇总。
5. `ResultFactory`：会话落盘和最终结果构造。

### 3. 旧边界被物理删除

`orchestration.local_agent`、`orchestration.agent_manager`、`budget.budget_guard` 已从生产代码物理删除，不再保留兼容 wrapper。

## Import Surface Changes

1. 生产侧不再存在 `from orchestration.local_agent import LocalAgent`。
2. 生产侧不再存在 `from orchestration.agent_manager import AgentManager`。
3. 生产侧不再存在 `from budget.budget_guard import BudgetGuard`。
4. `context_manager` 预检参数类型切换为 `RunLimits`。
5. main/CLI/query-engine 全部改用 `Agent`。

## Deleted Old Design

1. LocalAgent 巨石式“单类全栈编排”。
2. AgentManager 作为 orchestration 独立边界。
3. BudgetGuard 作为 budget 包独立边界。

## Verification

已通过的 Step 7 验证命令：

1. `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/agent -v`
2. `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_query_engine.py" -v`
3. `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_main*.py" -v`
4. `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v`

本轮验收结果：

1. `test/agent` 共 64 项通过。
2. `test_query_engine.py` 共 4 项通过。
3. `test_main*.py` 共 27 项通过。
4. 全量 `test/` 共 341 项通过。

## Boundary After Step 7

Step 7 到此停止。

当前已完成的是“agent 核心子系统重建与旧边界删除”；下一步尚未开始的是 Step 8（app 控制面重建）。
