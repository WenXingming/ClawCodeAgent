# ISSUE-009 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/context/__init__.py` | 新建 | 建立 context 子包统一导出入口 |
| `src/context/token_budget.py` | 新建 | `TokenBudgetSnapshot` + token 预检与估算函数 |
| `src/context/budget_guard.py` | 新建 | `BudgetGuard` 五维预算闸门与私有 `_check_*` 子方法 |
| `src/agent_runtime.py` | 修改 | `_execute_loop` 接入 token / cost / tool_calls / model_calls / session_turns 闸门 |
| `test/test_token_budget.py` | 新建 | 15 个 token 估算与预算快照单测 |
| `test/test_agent_runtime.py` | 追加 | 5 个预算闸门集成测试 |
| `docs/FINAL_ARCHITECTURE_PLAN.md` | 追加 | ISSUE-009 实施决策归档 |

## 关键设计决策

### 1. token 估算采用 char/4 启发式
`token_budget.py` 采用 `1 token ≈ 4 chars` 的近似规则；每条消息额外计入结构开销，
工具 schema 先序列化再估算。这样可在不引入真实 tokenizer 依赖的前提下，稳定完成调用前预检。

### 2. 明确区分软超限与硬超限
常量固定为：

- `OUTPUT_RESERVE_TOKENS = 4096`
- `SOFT_BUFFER_TOKENS = 13000`

其中：

- `is_hard_over` 用于直接阻断模型调用，返回 `stop_reason='token_limit'`
- `is_soft_over` 仅暴露上下文压力，供 ISSUE-010/011 的 snip/compact 读取

### 3. 本期同时落地全部 BudgetConfig 维度闸门
除 token 预检外，`BudgetGuard` 同步接管：

- `max_total_cost_usd`
- `max_tool_calls`
- `max_model_calls`
- `max_session_turns`

主循环不再散落内联判断，只保留 `check_pre_model()` 与 `check_post_tool()` 两个调用点。

### 4. token_budget event 始终记录
无论是否超限，每轮都会追加 `token_budget` event，记录：

- `projected_input_tokens`
- `hard_input_limit`
- `soft_input_limit`
- `is_hard_over`
- `is_soft_over`

这样后续上下文治理模块可以直接观测 prompt 压力，而无需重新计算。

### 5. 各预算闸门在主循环中有固定触发位置
为保证语义稳定，5 个预算维度分别固定在下列位置检查：

- `session_turns`：每轮最前，含 resume 的 `turns_offset`
- `model_calls`：每轮最前，在 token/cost 前
- `token`：模型调用前预检
- `cost`：模型调用前，以 `cost_baseline + usage_delta` 为准
- `tool_calls`：每次工具执行后立即检查

## 测试覆盖（新增 +20）

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test_token_budget` | message/token 估算基础（4 个） | 空消息、内容长度、多模态 list、非字符串内容的估算路径正确 |
| `test_token_budget` | messages/tools 聚合估算（4 个） | 空消息列表、多消息累积、空工具、非空工具 schema 投影正确 |
| `test_token_budget` | TokenBudgetSnapshot 语义（7 个） | 无上限、不过限、软超限、硬超限、双超限、snapshot 投影字段、soft limit 下限为 0 |
| `test_agent_runtime` | `test_run_stops_on_token_limit` | token 硬超限在模型调用前拦截 |
| `test_agent_runtime` | `test_run_stops_on_cost_limit` | 累计成本超限在模型调用前拦截 |
| `test_agent_runtime` | `test_run_stops_on_tool_call_limit` | 第一个工具执行后即触发 tool_call_limit |
| `test_agent_runtime` | `test_run_stops_on_model_call_limit` | 第 2 轮开始前拦截第二次模型调用 |
| `test_agent_runtime` | `test_run_stops_on_session_turns_limit_with_offset` | resume 场景会计入历史 turns 偏移 |

## 回归结果

补文档完成后再次运行 `python -m unittest discover -s test -v`：

- 测试数：152
- 结果：全部 OK