# ISSUE-029 Implementation Memory - Step 1: openai_client 域网关化

## 目标

在第二阶段 Phase 2 的第一步完成 openai_client 域收口：

1. 提炼跨模块契约到 core_contracts。
2. 建立 openai_client 统一网关并完成异常翻译。
3. 调整所有 src 调用方走网关，不再直接依赖 openai_client 内部实现。
4. 同步修复受影响测试。

## 契约提炼

新增 `src/core_contracts/openai_contracts.py`：

- `ModelClient`：跨模块最小模型调用接口（complete/stream/complete_stream + model_config）。
- `ModelGatewayError`：模型网关基础异常。
- `ModelConnectionError`：连接层异常（继承 GatewayTransportError）。
- `ModelTimeoutError`：超时异常（继承 GatewayTransportError）。
- `ModelResponseError`：响应结构异常（继承 GatewayValidationError），保留 `status_code` 与 `detail`。

## 网关构筑

新增 `src/openai_client/openai_client_gateway.py`：

- `OpenAIClientGateway` 是 openai_client 域唯一跨模块入口。
- 输入可为 `OpenAIClient` 实例或 `ModelConfig`，便于运行时装配。
- 统一把内部异常映射到 `core_contracts.openai_contracts`：
  - `OpenAIResponseError` -> `ModelResponseError`
  - `OpenAITimeoutError` -> `ModelTimeoutError`
  - `OpenAIConnectionError` -> `ModelConnectionError`
  - 其他 `OpenAIClientError` -> `ModelGatewayError`

## 调用方精简与替换

### src 层导入收敛

- `from openai_client import ...` 已全部替换为：
  - `from openai_client.openai_client_gateway import OpenAIClientGateway`（运行时装配点）
  - `from core_contracts.openai_contracts import ModelClient/...`（协作者契约与异常）

### 关键调用方

- `src/app/runtime_builder.py`
  - 默认客户端类型改为 `OpenAIClientGateway`。
- `src/app/cli.py`
  - 默认注入 `OpenAIClientGateway`。
  - 顶层异常处理从 `OpenAIClientError` 收敛到 `ModelGatewayError`。
- `src/main.py`
  - 入口默认注入 `OpenAIClientGateway`。
- `src/agent/agent.py`
  - `client` 字段类型改为 `ModelClient`（去除 openai_client 内部类型泄漏）。
- `src/agent/result_factory.py`
  - `client` 字段类型改为 `ModelClient`。
- `src/agent/turn_coordinator.py`
  - `client` 字段类型改为 `ModelClient`。
- `src/context/compactor.py`
  - 不再导入 openai_client 内部异常类型。
  - context-length 判断改为基于异常属性（`status_code/detail`）+ 关键字，兼容 gateway 异常和历史测试替身。
- `src/context/context_manager.py`
  - 客户端类型收敛到 `ModelClient`。
  - reactive compact 错误捕获改为统一处理模型阶段异常并落盘 backend_error 事件。

## 测试同步

- 更新：
  - `test/context/test_compactor.py`
  - `test/context/test_context_compactor.py`
- 调整点：
  - context-length 错误断言改用 `ModelResponseError` / `ModelConnectionError`。

## 验证结果

- `test/context`：通过（88 tests）。
- `test/agent`：通过（64 tests）。
- `test_main*.py`：通过（27 tests）。
- 严格导入扫描：
  - `TOTAL_VIOLATIONS=27`
  - `OPENAI_VIOLATIONS=0`

说明：本步只歼灭 openai_client 域违规，其他 27 处属于后续域（context/session/interaction/agent/app）待分步处理。
