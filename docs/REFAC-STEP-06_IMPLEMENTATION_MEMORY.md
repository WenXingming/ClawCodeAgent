# REFAC Step 06 Implementation Memory

## Step Goal

根据 [docs/GLOBAL_REFACTOR_PLAN.md](docs/GLOBAL_REFACTOR_PLAN.md) 的 Step 6，本步目标是把 context 领域重建为单一门面结构：引入 `ContextManager`，将旧的预算投影、snip、compact 与 pre-model 编排职责重组为新的 context 子模块，并删除旧 `BudgetContextOrchestrator` 这条跨层历史边界。

## Completed Work

1. 新增 [src/context/context_manager.py](src/context/context_manager.py)，引入 `ContextManager`、`PreModelContextOutcome` 与 `ReactiveCompactOutcome`。
2. 新增 [src/context/budget_projection.py](src/context/budget_projection.py)，用 `BudgetProjector` / `BudgetProjection` 替换旧 `ContextTokenBudgetEvaluator` / `ContextTokenBudgetSnapshot`。
3. 新增 [src/context/snipper.py](src/context/snipper.py)，用 `Snipper` 替换旧 `ContextSnipper`。
4. 新增 [src/context/compactor.py](src/context/compactor.py)，用 `Compactor` 替换旧 `ContextCompactor`。
5. 更新 [src/context/__init__.py](src/context/__init__.py)，通过惰性导出只暴露 `ContextManager`，同时规避 `context -> agent.run_state -> context` 的循环依赖。
6. 更新 [src/orchestration/local_agent.py](src/orchestration/local_agent.py)，删除对 evaluator / snipper / compactor / orchestrator 的直接持有，改为只通过 `ContextManager` 执行 pre-model 与 reactive compact 流程。
7. 更新 [src/interaction/slash_commands.py](src/interaction/slash_commands.py)，让 `/context` 改为通过 `ContextManager.project_budget()` 提供预算投影视图。
8. 更新 [src/agent/run_state.py](src/agent/run_state.py) 与 [src/budget/budget_guard.py](src/budget/budget_guard.py)，统一切换到 `BudgetProjection`。
9. 删除以下旧生产边界：
   - [src/context/context_token_budget_evaluator.py](src/context/context_token_budget_evaluator.py)
   - [src/context/context_snipper.py](src/context/context_snipper.py)
   - [src/context/context_compactor.py](src/context/context_compactor.py)
   - [src/orchestration/budget_context_orchestrator.py](src/orchestration/budget_context_orchestrator.py)
10. 迁移并对齐 context 测试文件命名：
   - [test/context/test_budget_projection.py](test/context/test_budget_projection.py)
   - [test/context/test_snipper.py](test/context/test_snipper.py)
   - [test/context/test_compactor.py](test/context/test_compactor.py)
   - [test/context/test_context_manager.py](test/context/test_context_manager.py)
11. 更新 [docs/Architecture.md](docs/Architecture.md)，使当前活文档与新的 context 门面结构、测试拓扑和当前 interaction/tools 目录一致。

## Production Refactor Outcome

### 1. agent 只调用 `ContextManager`

[src/orchestration/local_agent.py](src/orchestration/local_agent.py) 现在只持有一个 `context_manager` 字段。主循环中的 pre-model 检查、snip、auto compact 和 reactive compact 重试都统一通过 `ContextManager` 完成，agent 不再直接了解 context 领域内部的实现对象。

### 2. context 领域内部边界被重组为清晰分层

1. `ContextManager` 负责上下文治理总编排。
2. `BudgetProjector` 负责 token 预算投影。
3. `Snipper` 负责 tombstone 剪裁。
4. `Compactor` 负责 auto compact 与 reactive compact。
5. `ContextTokenEstimator` 继续作为内部通用 token 估算能力，被 projector / snipper / compactor 复用。

### 3. slash `/context` 与 budget guard 已对齐新模型

1. `/context` 不再直连旧 evaluator，而是通过 `ContextManager` 读取当前预算投影。
2. `BudgetGuard` 的 pre-model 检查已消费新的 `BudgetProjection`，与新的 context 门面结构保持一致。

## Import Surface Changes

本步同步完成了以下 import 面收敛：

1. 生产代码对 context 的公共入口收口为 `from context import ContextManager`。
2. `LocalAgent` 删除 `budget_evaluator`、`context_snipper`、`context_compactor`、`budget_context_orchestrator` 字段，仅保留 `context_manager`。
3. `SlashCommandDispatcher` 删除对旧预算 evaluator 的依赖。
4. `AgentRunState` 与 `BudgetGuard` 的预算快照类型切换为 `BudgetProjection`。
5. `context/__init__.py` 通过惰性 `__getattr__` 导出 `ContextManager`，避免重新引入循环导入。

## Deleted Old Design

本步明确删除的旧设计：

1. `BudgetContextOrchestrator` 作为独立 orchestration 边界的存在。
2. `LocalAgent` 对 context evaluator / snipper / compactor 的并列直接持有。
3. `slash_commands.py` 对旧预算 evaluator 的直连。
4. 旧 `context_token_budget_evaluator.py`、`context_snipper.py`、`context_compactor.py` 这组三文件命名与职责布局。

## Documentation Updates

本步同步更新了以下活文档：

1. [docs/Architecture.md](docs/Architecture.md)
2. [docs/REFAC-STEP-06_IMPLEMENTATION_MEMORY.md](docs/REFAC-STEP-06_IMPLEMENTATION_MEMORY.md)

## Verification

已通过的 Step 6 验证命令：

1. `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/context -v`
2. `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v`

本轮验收结果：

1. `test/context` 共 49 项通过，覆盖 `BudgetProjector`、`Snipper`、`Compactor`、`ContextManager` 与 `ContextTokenEstimator`。
2. 全量 `test/` 回归共 300 项通过。

## Boundary After Step 6

Step 6 到此停止。

当前已完成的是“context 领域门面化与内部模块重组”；下一步尚未开始的是 Step 7。