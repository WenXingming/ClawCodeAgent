# ISSUE-007 开发记忆（会话持久化与基础恢复）

## 1. 本次目标

完成 `FINAL_ARCHITECTURE_PLAN` 中 ISSUE-007 的最小可运行实现：

1. 定义 `AgentSessionSnapshot` 落盘结构。
2. 提供 `AgentSessionStore.save(...)` / `AgentSessionStore.load(...)`。
3. 接入 `LocalCodingAgent.run()` 结束自动保存。
4. 保证会话文件能恢复基础消息与配置对象。

## 2. 实现范围

### 已完成

1. 新增 `src/session/session_contracts.py`：
   - 定义 `AgentSessionSnapshot`
   - 复用现有 `ModelConfig` / `AgentRuntimeConfig` / `TokenUsage` 的 `to_dict()` / `from_dict()`
   - 支持 `schema_version` 与常见 camelCase 历史字段
2. 新增 `src/session/session_store.py`：
   - `AgentSessionStore.save(...)`
   - `AgentSessionStore.load(...)`
   - 基础 `session_id` 校验与路径解析
3. 扩展 `src/runtime/agent_runtime.py`：
   - `run()` 开始生成稳定 `session_id`
   - 所有终止路径统一走 `_build_run_result(...)`
   - 自动计算 `total_cost_usd`
   - 自动保存 session，并把 `session_id/session_path` 回填到 `AgentRunResult`
4. 调用侧直接实例化 `AgentSessionStore`：
   - 不再保留模块级 save/load 包装函数
   - CLI / runtime / tests 全部切换到实例方法
5. 新增/扩展测试：
   - `test/session/test_session_store.py`
   - `test/session/test_session_contracts.py`
   - `test/runtime/test_agent_runtime.py`

### 未实现（按计划故意延后）

1. `resume(prompt, stored_session)` 的连续执行语义。
2. 预算继承、同 session 续跑、plugin state 恢复。
3. 复杂 replay 提示与跨版本迁移工具。
4. session 级 scratchpad 目录自动创建与治理。

## 3. 边界与约束沉淀

1. 每次 `run()` 只保存一个最终 session 文件，不做中间 checkpoint。
2. `messages` 是后续 ISSUE-008 恢复上下文的基础来源，`transcript/events` 主要用于审计和排障。
3. `AgentSessionStore(directory).load(session_id)` 是唯一的恢复入口。
4. 缺失可选字段时走默认值；损坏 JSON、缺少核心字段或 `session_id` 非法时抛 `ValueError`。
5. 会话保存失败直接抛出，不做静默降级。

## 4. 设计决策（简洁优先）

1. 会话契约下沉到 `src/session/session_contracts.py`：
   - `AgentSessionSnapshot` 与会话存储职责同域收拢，降低 `contract_types.py` 体积。
2. `src/session/session_store.py` 保持薄 IO 层：
   - `directory` 作为实例状态挂在 `AgentSessionStore` 上。
   - 只管路径、UTF-8 JSON 读写和基础校验。
   - 不重复实现 `serialize_*` / `deserialize_*`。
3. runtime 统一收尾：
   - 正常结束、`max_turns`、`backend_error` 都走同一个保存出口，避免分叉遗漏。
4. 配置对象使用强类型恢复：
   - `AgentSessionSnapshot` 内部直接持有 `ModelConfig` / `AgentRuntimeConfig` / `TokenUsage`，方便 ISSUE-008 直接接续。

## 5. 验收标准映射（DoD）

DoD 来源：`docs/FINAL_ARCHITECTURE_PLAN.md`。

1. 每次 run 都产出 session 文件：✅
   - 证据：`test_run_without_tool_calls_returns_immediately`
   - 证据：`test_run_single_tool_call_chain`
   - 证据：`test_run_stops_with_max_turns`
   - 证据：`test_run_returns_backend_error_when_model_call_fails`
2. 能从文件恢复基础消息和配置：✅
   - 证据：`test_load_restores_config_objects`
   - 证据：`AgentSessionSnapshot.from_dict(...)` 直接恢复强类型配置对象
3. usage/cost 字段不丢失：✅
   - 证据：`test_save_and_load_round_trip`
   - 证据：`test_run_single_tool_call_chain`

## 6. 测试与结果

执行命令：

```powershell
python -m unittest test.session.test_session_contracts test.core_contracts.test_core_contracts test.runtime.test_agent_runtime test.session.test_session_store -v
python -m unittest test.openai_client.test_openai_client test.openai_client.test_openai_client_streaming -v
python -m unittest discover -s test -v
```

结果：

1. `test.session.test_session_contracts + test.core_contracts.test_core_contracts + test.runtime.test_agent_runtime + test.session.test_session_store`：32/32 通过。
2. `test.openai_client.test_openai_client + test.openai_client.test_openai_client_streaming`：22/22 通过。
3. 全量 `discover`：79/79 通过。

## 7. 对后续 ISSUE-008 的交接建议

1. `AgentSessionStore(...).load()` 已经能恢复配置对象、messages、usage 和 stop reason，ISSUE-008 可直接在此基础上恢复 `AgentSessionState`。
2. 当前 `session_id` 已在整个 `run()` 生命周期内稳定，ISSUE-008 可沿用同一 `session_id` 继续保存，避免 session 漂移。
3. 若后续需要预算继承，优先从 `AgentSessionSnapshot.usage / total_cost_usd / turns / tool_calls` 建立基线，而不是重新扫描 transcript。
