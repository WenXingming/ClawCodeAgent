# ISSUE-008 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/session/session_state.py` | 新增方法 | `from_persisted(messages, transcript, tool_call_count)` |
| `src/session/session_store.py` | 修改 | `FileNotFoundError` → `ValueError`，与损坏文件语义统一 |
| `src/orchestration/local_agent.py` | 重构 | `run` 瘦身 + 新增 `resume` + 提取 `_execute_loop` |
| `src/main.py` | 修改 | `--session-id` 参数 + resume 执行分支 |
| `test/session/test_session_state.py` | 新建 | 4 个 from_persisted 单测 |
| `test/orchestration/test_local_agent.py` | 追加 | 4 个 resume 集成测试 |
| `test/test_main.py` | 追加 | 3 个 CLI resume 路径测试 |
| `README.md` | 追加 | Resume 使用示例与常见错误 |
| `docs/architecture/FINAL_ARCHITECTURE_PLAN.md` | 追加 | ISSUE-008 实施决策 |

## 关键设计决策

### 1. 严格继承配置
resume 时不允许 CLI 参数覆盖 model/runtime 配置，确保跨会话行为一致。
`stored_session.model_config` 与 `stored_session.runtime_config` 全量继承。

### 2. 增量 cost 策略
```
total_cost_usd = cost_baseline + estimate_cost_usd(usage_delta)
```
历史成本固化存储，只重新估算本次 delta，避免计费策略变更造成历史成本偏差。

### 3. 共用 _execute_loop
`run` 与 `resume` 共用同一执行主循环，区别仅在于初始化参数：
- `run`：`turns_offset=0, usage_baseline=TokenUsage(), cost_baseline=0.0`
- `resume`：从 stored_session 取各基线值

### 4. plugin state 延后
本期不实现 plugin state 恢复，延至 ISSUE-014/016 插件 runtime 统一处理。

### 5. session_store 错误统一
`AgentSessionStore.load()` 对 `FileNotFoundError` 转为 `ValueError('Session not found: ...')`，
与损坏文件 `ValueError` 语义一致，main.py 只需单次 `except ValueError` 捕获。

## 测试覆盖（新增 +11）

| 测试文件 | 测试方法 | 验证点 |
|----------|----------|--------|
| test/session/test_session_state.py | test_from_persisted_restores_messages_and_count | 消息列表与 tool_call_count 完整恢复 |
| test/session/test_session_state.py | test_from_persisted_empty_transcript_falls_back_to_messages | transcript 为空时 fallback |
| test/session/test_session_state.py | test_from_persisted_preserves_nonempty_transcript | 已有 transcript 不被覆盖 |
| test/session/test_session_state.py | test_from_persisted_then_append_user_extends_state | 恢复后可继续追加消息 |
| test/orchestration/test_local_agent.py | test_resume_session_id_does_not_drift | session_id 不漂移 |
| test/orchestration/test_local_agent.py | test_resume_accumulates_usage_turns_and_tool_calls | 预算累计正确 |
| test/orchestration/test_local_agent.py | test_resume_model_sees_history_context | 上下文连续可见 |
| test/orchestration/test_local_agent.py | test_resume_backend_error_preserves_session_id_and_saves | 错误时 session_id 保持 |
| test/test_main.py | test_main_session_id_triggers_resume_not_run | --session-id 走 resume 路径 |
| test/test_main.py | test_main_resume_missing_session_returns_error | 不存在时 exit=2 + stderr 输出 |
| test/test_main.py | test_main_without_session_id_still_runs_normally | 无 --session-id 无回归 |

## 回归结果

运行 `python -m unittest discover -s test -v`：

- 测试数：94（原有 83 + 本期新增 11）
- 结果：全部 OK


