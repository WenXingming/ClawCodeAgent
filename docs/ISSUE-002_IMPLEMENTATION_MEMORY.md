# ISSUE-002 开发记忆（OpenAI-compatible 非流式客户端）

## 1. 本次目标

完成 `FINAL_ARCHITECTURE_PLAN` 中 ISSUE-002 的最小可运行实现：

1. 实现非流式 `complete` 调用。
2. 兼容 `tool_calls` 和 `usage` 解析。
3. 统一错误封装与异常语义。
4. 提供可回归的最小测试集。

## 2. 实现范围

### 已完成

1. 在 `src/contract_types.py` 新增 `OneTurnResponse` 契约对象：
   - 字段：`content`、`tool_calls`、`finish_reason`、`usage`
   - 方法：`to_dict` / `from_dict`
2. 在 `src/openai_client/openai_client.py` 新增非流式客户端：
   - `OpenAIClient.complete(...)`
   - 请求构造与响应解析
   - `tool_calls` 标准化解析（含 `function_call` 兼容）
   - `usage` 变体兼容（含 `prompt_eval_count` / `eval_count`）
3. 新增统一异常家族：
   - `OpenAIClientError`
   - `OpenAIConnectionError`
   - `OpenAITimeoutError`
   - `OpenAIResponseError`
4. 新增测试文件 `test/test_openai_client.py`（9 条）：
   - 正常文本响应
   - `tool_calls` 响应
   - `usage` 缺失与变体字段
   - 超时、连接失败、HTTP 错误
   - 响应结构非法
5. 在 `test/test_contract_types.py` 增加 `OneTurnResponse` 契约测试（2 条）。

### 未实现（按计划故意延后）

1. SSE 流式能力（ISSUE-003）。
2. 增量事件聚合与 UI 层渲染。
3. 重试、并发与连接池策略。

## 3. 设计决策

1. 客户端层与契约层分文件管理：
   - 契约集中在 `src/contract_types.py`
   - 调用逻辑集中在 `src/openai_client/openai_client.py`
2. 本轮只实现非流式 `complete`，避免跨 ISSUE 扩散。
3. 统一异常语义，调用方只需处理客户端异常家族。
4. 保持 ISSUE-001 的温和容错风格：
   - 字段缺失安全回退
   - 兼容 snake_case / camelCase / 常见别名

## 4. 验收结果（对应 ISSUE-002 DoD）

1. 能返回完整 `OneTurnResponse`：✅
2. `tool_calls` 可正确解析为 `ToolCall` 对象：✅
3. 错误时返回统一异常类型：✅

## 5. 测试与结果

执行命令：

```powershell
python -m unittest test/test_openai_client.py -v
python -m unittest test/test_contract_types.py -v
python -m unittest discover -s test -v
```

结果：

1. `test_openai_client.py`：9/9 通过。
2. `test_contract_types.py`：14/14 通过。
3. 全量 `discover`：23/23 通过。

## 6. 对后续（ISSUE-003）的交接点

1. `src/openai_client/openai_client.py` 已具备可复用的解析助手：
   - `_normalize_content`
   - `_parse_tool_arguments`
   - `_parse_usage`
2. ISSUE-003 可在不破坏 `complete` 的前提下增加 `stream` 能力。
3. 如需保持最小风险，建议 ISSUE-003 先加新接口和测试，再考虑抽取公共解析模块。

## 7. 可读性维护补充（稳扎稳打）

为满足“先理解再扩展”的节奏，本次对 ISSUE-002 做了不改变行为的可读性维护：

1. 在 `src/openai_client/openai_client.py` 增加文件级注释，明确模块职责与边界。
2. 在 `test/test_openai_client.py` 增加文件级注释，说明测试覆盖目标。
3. 在 `src/openai_client/openai_client.py` 增加分区注释（异常、解析助手、客户端实现）。
4. 抽取小型辅助方法以降低主流程阅读负担：
   - `_build_request(...)`
   - `_extract_choice_and_message(...)`
   - `_normalize_finish_reason(...)`
   - `_parse_single_tool_call(...)`
   - `_parse_legacy_function_call(...)`
5. 在 `test/test_openai_client.py` 新增响应构造辅助函数 `_build_single_choice_payload(...)`，减少重复样板。
6. 细化 `OneTurnResponse` 属性注释，突出字段语义。

回归结果：

```powershell
python -m unittest test/test_openai_client.py -v
python -m unittest test/test_contract_types.py -v
python -m unittest discover -s test -v
```

结果：

1. 客户端测试 9/9 通过。
2. 契约测试 14/14 通过。
3. 全量测试 23/23 通过。

## 8. 命名重构补记（可读性优先）

本次按“可读性优先”完成命名迁移，并保持功能行为不变：

1. 类型与文件重命名：
   - `AssistantTurn` -> `OneTurnResponse`
   - `src/agent_types.py` -> `src/contract_types.py`
   - `src/openai_compat.py` -> `src/openai_client/openai_client.py`
2. OpenAI 客户端命名族重命名：
   - `OpenAICompatClient` -> `OpenAIClient`
   - `OpenAICompatError` -> `OpenAIClientError`
   - `OpenAICompatConnectionError` -> `OpenAIConnectionError`
   - `OpenAICompatTimeoutError` -> `OpenAITimeoutError`
   - `OpenAICompatResponseError` -> `OpenAIResponseError`
3. 测试文件同步重命名：
   - `test/test_agent_types.py` -> `test/test_contract_types.py`
   - `test/test_openai_compat.py` -> `test/test_openai_client.py`

命名迁移后验证：

```powershell
python -m unittest test/test_contract_types.py -v
python -m unittest test/test_openai_client.py -v
python -m unittest discover -s test -v
```

结果：

1. 命名迁移后 23/23 通过。
2. 未出现行为回归。
