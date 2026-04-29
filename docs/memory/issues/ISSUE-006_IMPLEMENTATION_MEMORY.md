# ISSUE-006 开发记忆（LocalAgent 最小闭环）

## 1. 本次目标

完成 `FINAL_ARCHITECTURE_PLAN` 中 ISSUE-006 的最小可运行实现：

1. 实现 run 主循环（模型调用 -> 工具执行 -> 再调用 -> 收敛）。
2. 支持 `tool_calls` 回填与多轮推进。
3. 达到停止条件后返回 `AgentRunResult`。
4. 保证 transcript 完整可追踪。

## 2. 实现范围

### 已完成

1. 新增 `src/session/session_state.py`（由原 `src/agent_session.py` 迁移）：
   - `AgentSessionState`
   - `create(...)`
   - `append_user(...)`
   - `append_assistant_turn(...)`
   - `append_tool_result(...)`
   - `to_messages(...)`
   - `transcript(...)`
2. 新增 `src/orchestration/local_agent.py`：
   - `LocalAgent`
   - `run(...)`
   - `_build_openai_tools(...)`
   - `_build_run_result(...)`
3. 当前调用侧直接从 `src/orchestration/local_agent.py` 与 `src/session/session_state.py` 导入公开能力，不再依赖源码根聚合导出。
4. 新增测试 `test/orchestration/test_local_agent.py`，覆盖 ISSUE-006 主场景与边界场景。

### 未实现（按计划故意延后）

1. resume 语义与状态继承（ISSUE-008）。
2. 自动压缩与上下文裁剪（后续 ISSUE）。
3. 预算闸门、复杂策略钩子、持久化落盘（ISSUE-007+）。

## 3. 边界与约束沉淀

1. 主循环上限：严格受 `runtime_config.max_turns` 控制。
2. 工具执行入口：统一走 `execute_tool(...)`，避免主循环分散处理权限与错误细节。
3. 停止条件：
   - 无工具调用（模型返回可收敛）。
   - 达到最大轮次。
   - 模型客户端异常（`backend_error`）。
4. transcript 结构：只保留最小追踪必需字段，保证可读且可测试。

## 4. 设计决策（简洁优先）

1. 会话状态独立：把会话消息管理拆到 `AgentSessionState`，主循环仅负责编排。
2. 契约复用：复用既有 `OneTurnResponse/ToolCall/ToolExecutionResult/AgentRunResult`，不新增平行数据结构。
3. 错误语义简化：模型异常直接终止并返回 `stop_reason='backend_error'`，便于调用侧处理。
4. 工具回填统一：assistant 记录 `tool_calls` 摘要，tool 消息记录 `tool_call_id` 与执行结果。

## 5. 验收标准映射（DoD）

DoD 来源：`docs/architecture/FINAL_ARCHITECTURE_PLAN.md`。

1. 可完成一次读-改-总结链路：✅
   - 证据：`test_run_single_tool_call_chain` 与 `test_run_multiple_tool_calls_in_one_turn`。
2. 达到停止条件后返回 `AgentRunResult`：✅
   - 证据：`test_run_without_tool_calls_returns_immediately`、`test_run_stops_with_max_turns`、`test_run_returns_backend_error_when_model_call_fails`。
3. transcript 完整可追踪：✅
   - 证据：`tool_call_id`、`role`、`metadata` 在测试中均有断言。

## 6. 测试与结果

执行命令：

```powershell
C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_local_agent.py" -v
C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v
```

结果：

1. `test/orchestration/test_local_agent.py`：5/5 通过。
2. 全量 `discover`：69/69 通过。

## 7. 对后续 ISSUE-007/008 的交接建议

1. ISSUE-007 可在当前 `AgentRunResult` 基础上接入 session 落盘，不影响 run 主循环。
2. ISSUE-008 的 resume 可以复用 `AgentSessionState` 的消息结构，按恢复状态继续 run。
3. 若后续要支持工具流式 UI，可在主循环中切换到 `execute_tool_streaming(...)`，不需要重写会话模型。


