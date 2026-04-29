# REFAC Step 01 Implementation Memory

## Step Goal

根据 [docs/architecture/GLOBAL_REFACTOR_PLAN.md](/docs/architecture/GLOBAL_REFACTOR_PLAN.md) 的 Step 1，删除旧聚合运行配置对象与 `src/core_contracts/config.py`，把静态契约拆分为更小的领域对象。

## Completed Work

1. 新建并落地以下契约模块：
   - `src/core_contracts/budget.py`
   - `src/core_contracts/model.py`
   - `src/core_contracts/permissions.py`
   - `src/core_contracts/runtime_policy.py`
   - `src/core_contracts/coercion.py`
2. 物理删除以下旧模块：
   - `src/core_contracts/config.py`
   - `src/core_contracts/_coerce.py`
3. 把源码主消费点切到显式静态契约：
   - `LocalAgent` 现在直接依赖 `WorkspaceScope`、`ExecutionPolicy`、`ContextPolicy`、`ToolPermissionPolicy`、`BudgetConfig`、`SessionPaths`
   - CLI 不再组装或传递统一的 runtime config 对象
   - session snapshot 不再持久化 `runtime_config`
   - slash、tool context、budget orchestrator、hook policy runtime 均已改为按职责接收小契约
4. 全量替换测试中的旧导入与旧构造方式，确保测试表达新的静态边界。

## Structural Outcome

静态配置职责现在被明确拆开：

1. `WorkspaceScope` 负责工作目录与工作区范围。
2. `ExecutionPolicy` 负责最大轮次、命令超时与输出上限。
3. `ContextPolicy` 负责 compact/snip 策略与结构化输出约束。
4. `ToolPermissionPolicy` 负责工具权限。
5. `BudgetConfig` 负责预算闸门。
6. `SessionPaths` 负责会话与 scratchpad 路径。

这一步没有引入新的兼容转发层，也没有保留旧的聚合配置对象。

## Verification

静态检查：

1. `get_errors` 全仓返回无错误。
2. `grep` 检查 `src/**` 与 `test/**` 中已不存在旧聚合运行配置对象符号与旧 config 聚合模块引用。

定向测试：

1. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/core_contracts -p "test_core_contracts.py" -v`
2. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/session -p "test_session_*.py" -v`
3. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/tools -v`
4. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/interaction -p "test_slash_commands.py" -v`
5. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/openai_client -v`
6. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_budget_context_orchestrator.py" -v`
7. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_query_engine.py" -v`
8. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_local_agent.py" -v`
9. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_main*.py" -v`

结果：

1. 上述切片均已通过。
2. `test_core_contracts.py` 共 21 项通过。
3. `test_local_agent.py` 共 39 项通过。

## Boundary After Step 1

Step 1 已完成“契约拆毁与替换”。

尚未完成的下一阶段工作仍然属于 [docs/architecture/GLOBAL_REFACTOR_PLAN.md](/docs/architecture/GLOBAL_REFACTOR_PLAN.md) 的 Step 2 及之后：

1. 进一步把 session / CLI 的职责从“能工作”推进到“结构上彻底重建”。
2. 引入 `AgentRunState`，收拢动态状态。
3. 继续拆分 `LocalAgent` 与 CLI 巨石。
