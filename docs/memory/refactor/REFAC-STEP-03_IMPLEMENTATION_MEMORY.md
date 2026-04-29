# REFAC Step 03 Implementation Memory

## Step Goal

根据 [docs/architecture/GLOBAL_REFACTOR_PLAN.md](/docs/architecture/GLOBAL_REFACTOR_PLAN.md) 的 Step 3，引入单一动态运行态对象 `AgentRunState`，把 turn、usage、events、budget snapshot、tool count、MCP capability window 与当前 turn 的有效工具表从 `LocalAgent` 的局部变量和 `AgentSessionState` 中收口出来。

## Completed Work

1. 新增 [src/agent/run_state.py](src/agent/run_state.py)，定义 `AgentRunState`。
2. `LocalAgent.run()` 与 `LocalAgent.resume()` 不再直接把动态状态拆成多段参数传递；统一先构造 `AgentRunState`，再进入受管调用与主循环。
3. [src/orchestration/budget_context_orchestrator.py](src/orchestration/budget_context_orchestrator.py) 的 `run_pre_model_cycle()` 与 `complete_with_reactive_compact()` 已改为直接读取并回写 `run_state`，删除了 `turns_offset / turns_this_run / usage_delta / model_call_count` 这条长参数链。
4. [src/session/session_state.py](src/session/session_state.py) 已瘦身为纯消息/转录容器：
   - 保留 `messages` 与 `transcript_entries`
   - 保留 `append_user()` / `append_assistant_turn()` / `append_tool_result()`
   - 删除工具调用计数与 MCP capability window 的内建状态
5. `LocalAgent` 内部关于工具计数与 MCP capability window 的逻辑，已全部迁移到 `AgentRunState`。
6. slash 控制面不再从 `AgentSessionState` 读取工具计数；[src/interaction/slash_commands.py](src/interaction/slash_commands.py) 的 `SlashCommandContext` 现在显式携带 `tool_call_count`。

## Structural Outcome

Step 3 后，运行时边界变成：

1. `AgentSessionState` 只负责消息与转录容器。
2. `AgentRunState` 负责一次 run/resume 调用的全部动态推进状态。
3. `LocalAgent` 的主循环围绕 `run_state` 推进，而不是维护一组不断扩散的局部变量。
4. `BudgetContextOrchestrator` 不再要求调用方显式传递多段运行态标量，减少了 orchestration 层之间的机械参数搬运。

## Test Updates

1. 新增 [test/agent/test_run_state.py](test/agent/test_run_state.py)，覆盖：
   - resume 基线恢复
   - turn/usage 聚合
   - 工具结果计数
   - MCP capability window 替换
2. 更新 [test/session/test_session_state.py](test/session/test_session_state.py)，删除对运行态计数与 MCP window 的断言，保留消息/转录容器行为验证。
3. 更新 [test/orchestration/test_budget_context_orchestrator.py](test/orchestration/test_budget_context_orchestrator.py)，改为通过 `AgentRunState` 驱动 orchestrator。
4. 更新 [test/interaction/test_slash_commands.py](test/interaction/test_slash_commands.py)，显式提供 `tool_call_count`。

## Verification

已通过的定向回归：

1. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/agent -p "test_run_state.py" -v`
2. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/session -p "test_session_state.py" -v`
3. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_budget_context_orchestrator.py" -v`
4. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/interaction -p "test_slash_commands.py" -v`
5. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_local_agent.py" -v`
6. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_query_engine.py" -v`

结果：

1. `test_run_state.py` 共 4 项通过。
2. `test_session_state.py` 共 4 项通过。
3. `test_budget_context_orchestrator.py` 共 2 项通过。
4. `test_slash_commands.py` 共 14 项通过。
5. `test_local_agent.py` 共 39 项通过。
6. `test_query_engine.py` 共 4 项通过。

## Boundary After Step 3

Step 3 只解决动态运行态收口与参数链清理。

尚未进入的下一阶段工作包括：

1. Step 4 的工具层重建。
2. 把 `LocalAgent` 中剩余的工具执行细节继续下沉到独立工具子系统。
3. 继续消解 orchestration 对 workspace/extensions/runtime 细节的直接认知。
