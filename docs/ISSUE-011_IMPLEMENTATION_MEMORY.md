# ISSUE-011 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/context/compact.py` | 新建 | `CompactResult`、auto/reactive compact 判定与摘要压缩逻辑 |
| `src/context/__init__.py` | 修改 | 导出 compact 公共接口 |
| `src/openai_client/openai_client.py` | 修改 | `OpenAIResponseError` 增加结构化 `status_code/detail` |
| `src/runtime/agent_runtime.py` | 修改 | 接入 auto compact 与 reactive compact retry |
| `test/context/test_compact.py` | 新建 | compact 单元测试 |
| `test/runtime/test_agent_runtime.py` | 追加 | auto compact / reactive compact 集成测试 |
| `test/openai_client/test_openai_client.py` | 追加断言 | 校验 HTTP 响应异常保留结构化信息 |
| `docs/Architecture.md` | 修改 | ContextPkg 追加 `compact.py` 节点与依赖 |
| `docs/FINAL_ARCHITECTURE_PLAN.md` | 追加 | ISSUE-011 实施决策归档 |

## 关键设计决策

### 1. auto compact 使用显式阈值，不复用 `is_soft_over`
`should_auto_compact(projected_input_tokens, auto_compact_threshold_tokens)`
只读取 `AgentRuntimeConfig.auto_compact_threshold_tokens`。这样 compact 的开启条件
与 snip 分离，保留了“轻量剪裁”和“摘要压缩”两个治理层级的独立开关。

### 2. compact 复用 snip 的保留区间规则
压缩时仍遵循：

- 前缀连续 `system` 消息保留
- 尾部 `compact_preserve_messages` 条最近消息保留
- 仅中间段参与压缩

compact 成功后，用两条 system reminder 替换中间段：

- compact boundary
- compact summary

### 3. reactive compact 只针对 context-length 类后端错误
`is_context_length_error()` 仅在后端响应语义明确属于 prompt-too-long /
context window exceeded 时返回 True。普通连接错误、超时或通用 backend_error
不进入 compact 恢复路径，保持原有失败语义。

### 4. compact 摘要调用禁用工具，输出纯文本摘要
`compact_conversation()` 调用模型时固定传 `tools=[]`，要求返回纯文本摘要，
摘要内容只保留继续任务所需的：用户目标、关键文件/工具、关键发现、当前下一步。
这样 compact 不会把“摘要阶段”误变成新的工具执行轮次。

### 5. compact 也计入模型调用与 usage 预算
auto compact 和 reactive compact 本质上都在调用模型，因此：

- `usage_delta += compact_result.usage`
- `model_call_count += 1`

这样 cost/model_calls 预算与真实后端开销一致；同一轮如果 compact 已耗尽预算，
主循环会在下一次 pre-model 检查中按既有预算闸门停止。

## 测试覆盖（新增 +15）

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test_compact` | threshold 判定（2 个） | `auto_compact_threshold_tokens` 为 None / 命中阈值时的触发语义 |
| `test_compact` | context-length 错误识别（3 个） | HTTP 413、HTTP 400 关键词、非 context error 的区分 |
| `test_compact` | compact prompt/summary（2 个） | preserved tail 不进入摘要请求、摘要空行规范化 |
| `test_compact` | `apply_compact_summary`（2 个） | prefix/tail 保留、无中间段时不压缩 |
| `test_compact` | `compact_conversation`（2 个） | 成功压缩时原地改写消息并统计 usage；空摘要时返回错误 |
| `test_agent_runtime` | `test_auto_compact_triggered_at_explicit_threshold` | 显式阈值命中时 auto compact 先于主模型调用发生 |
| `test_agent_runtime` | `test_auto_compact_not_triggered_when_threshold_not_met` | 阈值未命中时不 compact |
| `test_agent_runtime` | `test_reactive_compact_retries_on_context_length_error` | prompt-too-long 后触发 reactive compact 并重试成功 |
| `test_agent_runtime` | `test_reactive_compact_returns_backend_error_when_compaction_fails` | reactive compact 无进展时回落到 backend_error |
| `test_openai_client` | `test_complete_http_error_raises_response_error` | `OpenAIResponseError` 暴露 `status_code/detail` |

## 回归结果

补文档前已运行：

- `python -m unittest test.context.test_compact test.openai_client.test_openai_client -v` → 全部 OK
- `python -m unittest test.runtime.test_agent_runtime -v` → 全部 OK

补文档后再次运行 `python -m unittest discover -s test -v`：

- 测试数：167
- 结果：全部 OK