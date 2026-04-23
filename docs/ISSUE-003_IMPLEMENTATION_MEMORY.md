# ISSUE-003 开发记忆（OpenAI-compatible 客户端流式能力）

## 1. 本次目标

完成 `FINAL_ARCHITECTURE_PLAN` 中 ISSUE-003 的最小可运行实现：

1. 支持 SSE 流式读取与 `[DONE]` 终止。
2. 输出标准化流事件（`StreamEvent`）。
3. 支持内容增量、工具调用增量和 usage 聚合。
4. 保持接口简单好用，避免调用方重复拼装逻辑。

## 2. 实现范围

### 已完成

1. 在 `src/core_contracts/` 新增 `StreamEvent` 契约对象：
   - 字段：`type`、`delta`、`tool_call_index`、`tool_call_id`、`tool_name`、`arguments_delta`、`finish_reason`、`usage`、`raw_event`
   - 方法：`to_dict` / `from_dict`
2. 在 `src/openai_client/openai_client.py` 新增流式能力：
   - `OpenAIClient.stream(...) -> Iterator[StreamEvent]`
   - `OpenAIClient.complete_stream(...) -> OneTurnResponse`
   - `_iter_sse_payloads(...)` / `_decode_sse_payload(...)` / `_parse_stream_payload(...)`
3. 流式工具参数聚合：
   - 支持 `tool_call` 参数分片拼接。
   - 支持后续分片仅返回 `index` 不返回 `id` 的场景（通过 `index -> call_id` 映射归并）。
4. 错误语义保持统一：
   - 复用 `OpenAIClientError` 异常族。
   - 新增 `_raise_request_error(...)` 做网络异常统一映射。
5. 测试增强：
   - 新增 `test/openai_client/test_openai_client_streaming.py`。
   - 补充 `test/openai_client/test_openai_client.py` 边界测试（多 choice、非法 choices 类型）。
   - 补充 `test/core_contracts/test_core_contracts.py` 的 `ModelPricing` 与 `StreamEvent` 测试。

### 未实现（按计划故意延后）

1. UI 层渲染与交互（不在 ISSUE-003 范围）。
2. 主循环 runtime 的流式接入（可在后续 ISSUE 接力）。
3. 重试策略、连接池、并发调度。

## 3. 设计决策（简洁优先）

1. 保持双入口：
   - `stream(...)`：适合边收边处理。
   - `complete_stream(...)`：适合直接拿最终结果。
2. 严格流式解析：
   - SSE chunk 非法 JSON 直接抛错，不做静默吞错。
3. 可读性优先：
   - 用 `_ToolCallBuildState` 封装工具增量聚合状态。
   - 用 `_raise_request_error(...)` 消除重复异常映射逻辑。
4. 非侵入式升级：
   - 保持 `complete(...)` 语义不变，避免 ISSUE-002 回归。

## 4. 验收结果（对应 ISSUE-003 DoD）

1. 内容增量拼接正确：✅
2. 工具调用参数增量可恢复完整 JSON：✅
3. 结束事件与 usage 一致：✅

## 5. 测试与结果

执行命令：

```powershell
python -m unittest test.openai_client.test_openai_client_streaming -v
python -m unittest test.openai_client.test_openai_client -v
python -m unittest test.core_contracts.test_core_contracts -v
python -m unittest discover -s test -v
```

结果：

1. `test/openai_client/test_openai_client_streaming.py`：11/11 通过。
2. `test/openai_client/test_openai_client.py`：11/11 通过。
3. `test/core_contracts/test_core_contracts.py`：17/17 通过。
4. 全量 `discover`：39/39 通过。

## 6. 关键场景覆盖

1. 纯文本流：`content_delta` 顺序拼接。
2. 工具调用流：`arguments_delta` 分片恢复 JSON。
3. DONE-only 空流：仅输出 `message_start`，不会误报异常。
4. 异常流：HTTP/连接/超时/坏 JSON/坏 choices 类型均正确抛出统一异常。
5. 多 choice 非流式响应：明确只消费 `choices[0]`。

## 7. 后续开发交接建议

1. 若后续 runtime 需要实时渲染，可直接消费 `stream(...)` 事件，不必重复解析 SSE。
2. 若调用方只关心最终输出，优先用 `complete_stream(...)`，减少业务层拼装代码。
3. 后续 ISSUE 若涉及多模型适配，可保留当前 `StreamEvent` 结构作为统一事件层。
4. 后续接 ISSUE-004 时，建议先接入 `stream_model_responses` 开关到运行主流程。
