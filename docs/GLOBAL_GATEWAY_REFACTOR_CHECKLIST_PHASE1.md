# 全局网关化与契约重构 Checklist（Phase 1）

## 扫描基线

- 扫描范围：src/**/*.py
- 红线规则：跨模块导入仅允许
  - from {module}.{module}_gateway import XxxGateway
  - from core_contracts... import ...
- 当前结果：33 处违规导入（含 1 处文件编码异常导致解析失败）

## 0. 预处理阻塞项

- 编码阻塞：src/agent/turn_coordinator.py 存在 BOM（U+FEFF），静态扫描解析失败。
- 处理要求：统一写回 UTF-8 无 BOM，再继续该文件的 import 收敛与重构。

## 1. 网关缺口（需要新建/重构）

### 1.1 需要新建的 gateway

1. src/context/context_gateway.py
2. src/session/session_gateway.py
3. src/interaction/interaction_gateway.py
4. src/openai_client/openai_client_gateway.py
5. src/agent/agent_gateway.py
6. src/app/app_gateway.py

### 1.2 已存在 gateway（需保持唯一出口）

1. src/tools/tools_gateway.py
2. src/workspace/workspace_gateway.py

## 2. 违规导入清单（按文件）

### 2.1 agent 域

- src/agent/agent.py:14 from context import ...（应走 context.context_gateway）
- src/agent/agent.py:20 from interaction import ...（应走 interaction.interaction_gateway）
- src/agent/agent.py:21 from openai_client import ...（应走 openai_client.openai_client_gateway）
- src/agent/agent.py:22 from session import ...（应走 session.session_gateway）
- src/agent/agent.py:25 from workspace import ...（应走 workspace.workspace_gateway）
- src/agent/prompt_processor.py:17 from interaction import ...（应走 interaction.interaction_gateway）
- src/agent/prompt_processor.py:19 from workspace import ...（应走 workspace.workspace_gateway）
- src/agent/result_factory.py:12 from openai_client import ...（应走 openai_client.openai_client_gateway）
- src/agent/result_factory.py:13 from session import ...（应走 session.session_gateway）
- src/agent/run_limits.py:10 from context import ...（应走 context.context_gateway）
- src/agent/run_state.py:7 from context import ...（应走 context.context_gateway）
- src/agent/run_state.py:10 from session import ...（应走 session.session_gateway）
- src/agent/turn_coordinator.py:1 解析失败（BOM），需先修复编码再收敛导入

### 2.2 app 域

- src/app/chat_loop.py:9 from interaction import ...（应走 interaction.interaction_gateway）
- src/app/chat_loop.py:20 from session import ...（应走 session.session_gateway）
- src/app/cli.py:12 from interaction import ...（应走 interaction.interaction_gateway）
- src/app/cli.py:13 from openai_client import ...（应走 openai_client.openai_client_gateway）
- src/app/cli.py:14 from session import ...（应走 session.session_gateway）
- src/app/cli.py:271 from agent import ...（应走 agent.agent_gateway）
- src/app/query_service.py:7 from agent import ...（应走 agent.agent_gateway）
- src/app/runtime_builder.py:10 from agent import ...（应走 agent.agent_gateway）
- src/app/runtime_builder.py:16 from openai_client import ...（应走 openai_client.openai_client_gateway）
- src/app/runtime_builder.py:17 from session import ...（应走 session.session_gateway）

### 2.3 context 域

- src/context/compactor.py:12 from openai_client import ...（应走 openai_client.openai_client_gateway）
- src/context/context_manager.py:7 from agent.run_limits import ...（应走 agent.agent_gateway）
- src/context/context_manager.py:8 from agent.run_state import ...（应走 agent.agent_gateway）
- src/context/context_manager.py:16 from openai_client import ...（应走 openai_client.openai_client_gateway）

### 2.4 interaction 域

- src/interaction/slash_commands.py:14 from context import ...（应走 context.context_gateway）
- src/interaction/slash_commands.py:20 from session import ...（应走 session.session_gateway）

### 2.5 入口 main

- src/main.py:8 from app import ...（应走 app.app_gateway）
- src/main.py:9 from agent import ...（应走 agent.agent_gateway）
- src/main.py:10 from openai_client import ...（应走 openai_client.openai_client_gateway）
- src/main.py:11 from session import ...（应走 session.session_gateway）

## 3. 需要提炼到 core_contracts 的泄漏契约

### 3.1 context_contracts.py（新建）

- BudgetProjectionDTO（替代直接暴露 context.budget_projection.BudgetProjection）
- PreModelContextOutcomeDTO
- ReactiveCompactOutcomeDTO

### 3.2 session_contracts.py（新建）

- SessionSnapshotDTO（外部可见最小字段）
- SessionStateDTO（消息、转录、计数等外部最小视图）

### 3.3 interaction_contracts.py（新建）

- SlashCommandOutcomeDTO
- InteractionInputDTO / InteractionRenderDTO（用于 app 与 agent 间传输）

### 3.4 openai_contracts.py（评估后按需新建）

- 若现有 core_contracts.protocol 无法覆盖 client 层错误/响应摘要，则补充：
  - ModelCallSummaryDTO
  - Gateway 级 OpenAI 归一化错误（映射到 core_contracts.gateway_errors）

## 4. “巨石粉碎”重塑策略（不是搬运）

### 4.1 app 层合并编排

- 问题：chat_loop.py、runtime_builder.py、cli.py 存在重复装配与会话/客户端拼接逻辑。
- 重塑：通过 AppGateway 暴露 2-3 个极简用例方法（build_runtime、run_chat_once、resume_chat_once），删除重复分支与中间适配层。

### 4.2 agent 层去多域直连

- 问题：agent.py、result_factory.py、run_state.py 直接感知 session/context/openai/interaction。
- 重塑：AgentGateway 只依赖 core_contracts + 各域 gateway；turn_coordinator 内只保留流程控制，删除跨域转换与冗余状态拼装代码。

### 4.3 context 层依赖反转

- 问题：context_manager.py 反向导入 agent.run_limits / agent.run_state（违反分层）。
- 重塑：把运行时最小输入改为 core_contracts DTO；ContextGateway 接口参数不再接受 agent 内部类型。

### 4.4 interaction 层收口

- 问题：slash_commands.py 直接读取 context/session 细节。
- 重塑：InteractionGateway 仅接收 SessionStateDTO 与 ContextSummaryDTO；命令处理结果统一为 DTO，删除直接内部模型读写。

## 5. Phase 2 建议执行顺序（单步闭环）

1. openai_client 域（最小外部面，先稳定模型调用与错误翻译）
2. context 域（处理反向依赖，解除 context -> agent）
3. session 域（统一快照/状态 DTO 出口）
4. interaction 域（slash 命令接口化）
5. agent 域（聚合调用，粉碎 turn_coordinator 冗余）
6. app/main 域（最后收口入口）

每完成一个域：契约 -> 网关 -> 调用方精简 -> 测试文档 -> 强制暂停。
