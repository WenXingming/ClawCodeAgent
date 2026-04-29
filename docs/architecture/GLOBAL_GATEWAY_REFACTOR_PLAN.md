# Global Anti-Leak & Gateway Convergence Plan

## 0. 目标与边界

本计划用于 Phase 1 全局扫雷，目标是把跨模块依赖收敛为两类唯一入口：

1. from {module}.{module}_gateway import XxxGateway
2. from core_contracts.{contract_module} import Xxx

除上述两类外，跨模块导入一律视为违规。

---

## 1. 当前违规面扫描结论

### 1.1 已有 Gateway 文件

- src/workspace/workspace_gateway.py
- src/tools/tool_gateway.py

说明：tools 当前命名不满足强制规范（应为 tools_gateway.py）。

### 1.2 主要违规类型

1. 跨模块直接导入领域内部实现
- 例如 agent 直接导入 tools.registry、tools.executor、tools.mcp
- 例如 interaction 直接导入 tools.registry

2. 跨模块直接导入领域包导出（非 xxx_gateway）
- 例如 app/agent/context 从 session、openai_client、interaction、workspace 直接导入
- 该方式在本轮规范下仍违规，必须改为 xxx_gateway 导入。

3. 跨模块直接消费底层异常和内部 DTO
- 例如 MCPTransportError、ToolExecutionError、SearchQueryError、AgentSessionState 等在调用方外泄

---

## 2. 重构前后目录树（核心对比）

## 2.1 重构前（核心节选）

```text
src/
  agent/
    agent.py
    turn_coordinator.py
    run_state.py
  app/
    cli.py
    runtime_builder.py
  context/
    context_manager.py
    budget_projection.py
    compactor.py
  interaction/
    slash_commands.py
  openai_client/
    openai_client.py
  planning/
    plan_runtime.py
    planning_service.py
  session/
    session_manager.py
    session_snapshot.py
  tools/
    tool_gateway.py
    registry.py
    executor.py
    mcp/
  workspace/
    workspace_gateway.py
    search_service.py
    plugin_catalog.py
  core_contracts/
    protocol.py
    run_result.py
    runtime_policy.py
    ...
```

## 2.2 重构后（目标形态）

```text
src/
  agent/
    agent_gateway.py
    agent.py
    turn_coordinator.py
    ...
  app/
    app_gateway.py
    cli.py
    runtime_builder.py
    ...
  context/
    context_gateway.py
    context_manager.py
    ...
  interaction/
    interaction_gateway.py
    slash_commands.py
    ...
  openai_client/
    openai_client_gateway.py
    openai_client.py
  planning/
    planning_gateway.py
    planning_service.py
    ...
  session/
    session_gateway.py
    session_manager.py
    ...
  tools/
    tools_gateway.py
    executor.py
    registry.py
    mcp/
  workspace/
    workspace_gateway.py
    ...
  core_contracts/
    gateway_errors.py
    tools_contracts.py
    session_contracts.py
    workspace_contracts.py
    context_contracts.py
    planning_contracts.py
    openai_contracts.py
    agent_contracts.py
    app_contracts.py
    ...
```

说明：重构后每个领域仅允许通过 {module}_gateway.py 暴露服务；现有非 gateway 的跨模块导入全部清零。

---

## 3. Gateway 建设清单（必须新建/重构）

## 3.1 tools 领域

目标文件：src/tools/tools_gateway.py

动作：

1. 重命名并重构 src/tools/tool_gateway.py -> src/tools/tools_gateway.py
2. 对外只暴露 ToolsGateway
3. 将 ToolExecutionError、ToolPermissionError、MCPTransportError 等内部异常统一翻译为 core_contracts.gateway_errors 中的通用异常
4. 将 LocalTool、ToolStreamUpdate、MCPTool 等内部结构对外替换为 core_contracts.tools_contracts DTO

当前高风险泄漏调用方：

- src/agent/agent.py
- src/agent/turn_coordinator.py
- src/agent/prompt_processor.py
- src/interaction/slash_commands.py
- src/workspace/plugin_catalog.py
- src/workspace/policy_catalog.py

## 3.2 session 领域

目标文件：src/session/session_gateway.py

动作：

1. 抽离 SessionGateway（封装 SessionManager）
2. 对外不再暴露 AgentSessionSnapshot/AgentSessionState 内部实现
3. 统一输出 core_contracts.session_contracts DTO（如 SessionSnapshotDTO、SessionStateDTO）
4. 将 session 领域异常翻译为 core_contracts.gateway_errors

当前高风险泄漏调用方：

- src/app/cli.py
- src/app/chat_loop.py
- src/app/runtime_builder.py
- src/agent/agent.py
- src/agent/turn_coordinator.py
- src/agent/result_factory.py
- src/agent/run_state.py
- src/interaction/slash_commands.py
- src/main.py

## 3.3 context 领域

目标文件：src/context/context_gateway.py

动作：

1. 抽离 ContextGateway（封装 ContextManager）
2. BudgetProjection、CompactionResult 等返回结构下沉/翻译到 core_contracts.context_contracts
3. 禁止 agent、interaction 直接依赖 context 内部模型

当前高风险泄漏调用方：

- src/agent/agent.py
- src/agent/turn_coordinator.py
- src/agent/run_limits.py
- src/agent/run_state.py
- src/interaction/slash_commands.py

## 3.4 workspace 领域

目标文件：src/workspace/workspace_gateway.py（保留并重构）

动作：

1. 保持单一入口 WorkspaceGateway
2. SearchResponse/SearchResult/SearchQueryError 等对外类型迁移到 core_contracts.workspace_contracts
3. Gateway 内翻译 search/worktree/plugin/policy 子系统异常
4. 调整 workspace/__init__.py，移除对内部服务类的大量导出

当前高风险泄漏调用方：

- src/agent/prompt_processor.py
- src/agent/agent.py
- src/agent/turn_coordinator.py

## 3.5 planning 领域

目标文件：src/planning/planning_gateway.py

动作：

1. 从 PlanningService 迁移到 PlanningGateway 作为唯一跨模块 API
2. TaskRecord/TaskStatus/PlanStep 对外改为 core_contracts.planning_contracts DTO
3. workflow_runtime/plan_runtime 的内部耦合仅在领域内保留

当前高风险泄漏调用方：

- 目前跨模块泄漏较少，但需要统一到 gateway 纪律

## 3.6 openai_client 领域

目标文件：src/openai_client/openai_client_gateway.py

动作：

1. 建立 OpenAIClientGateway 作为唯一跨模块入口
2. OpenAIClientError/OpenAIResponseError 等内部异常翻译为 core_contracts.openai_contracts + gateway_errors
3. 屏蔽直接暴露具体 client 实现

当前高风险泄漏调用方：

- src/app/cli.py
- src/app/runtime_builder.py
- src/main.py
- src/context/context_manager.py
- src/context/compactor.py
- src/agent/agent.py
- src/agent/turn_coordinator.py
- src/agent/result_factory.py

## 3.7 interaction 领域

目标文件：src/interaction/interaction_gateway.py

动作：

1. 将渲染/slash 调度能力封装为 InteractionGateway
2. 外部不直接依赖 SlashCommandDispatcher、渲染器细节
3. 命令/渲染结果类型抽取到 core_contracts.app_contracts

当前高风险泄漏调用方：

- src/app/cli.py
- src/app/chat_loop.py
- src/agent/agent.py
- src/agent/prompt_processor.py

## 3.8 agent 与 app 顶层领域（建议纳入统一纪律）

目标文件：

- src/agent/agent_gateway.py
- src/app/app_gateway.py

动作：

1. 使 main 仅依赖 app_gateway / agent_gateway
2. app 内部对 runtime_builder/chat_loop 的直接耦合经 AppGateway 收敛
3. agent 对外暴露统一执行接口与统一异常面

---

## 4. core_contracts 抽取清单（DTO/异常/接口）

## 4.1 新增通用异常层

目标文件：src/core_contracts/gateway_errors.py

建议新增：

1. GatewayError（基类）
2. GatewayPermissionError
3. GatewayTransportError
4. GatewayValidationError
5. GatewayRuntimeError
6. GatewayNotFoundError

翻译映射（示例）：

- tools.executor.ToolPermissionError -> GatewayPermissionError
- tools.mcp.MCPTransportError -> GatewayTransportError
- workspace.search_service.SearchQueryError -> GatewayValidationError
- openai_client.OpenAIResponseError -> GatewayRuntimeError

## 4.2 tools 契约

目标文件：src/core_contracts/tools_contracts.py

建议抽取：

1. ToolDescriptorDTO（替代 LocalTool 外泄）
2. ToolCallDTO
3. ToolResultDTO
4. ToolStreamChunkDTO
5. MCPCapabilityDTO / MCPToolDTO（仅保留通用字段）

## 4.3 session 契约

目标文件：src/core_contracts/session_contracts.py

建议抽取：

1. SessionSnapshotDTO
2. SessionStateDTO
3. SessionPersistResultDTO

## 4.4 workspace 契约

目标文件：src/core_contracts/workspace_contracts.py

建议抽取：

1. SearchQueryDTO
2. SearchResultDTO
3. SearchResponseDTO
4. WorktreeStatusDTO
5. PluginPolicyDecisionDTO

## 4.5 context 契约

目标文件：src/core_contracts/context_contracts.py

建议抽取：

1. BudgetProjectionDTO
2. CompactionResultDTO
3. ContextUsageDTO

## 4.6 planning 契约

目标文件：src/core_contracts/planning_contracts.py

建议抽取：

1. PlanStepDTO
2. TaskRecordDTO
3. TaskTransitionDTO

## 4.7 openai 契约

目标文件：src/core_contracts/openai_contracts.py

建议抽取：

1. ModelRequestDTO
2. ModelResponseDTO
3. ModelUsageDTO

## 4.8 app/agent 跨层契约

目标文件：

- src/core_contracts/agent_contracts.py
- src/core_contracts/app_contracts.py

建议抽取：

1. AgentRunRequestDTO / AgentRunResponseDTO
2. ResumeRequestDTO
3. SlashCommandRequestDTO / SlashCommandResponseDTO

---

## 5. 调用方净化清单（重点文件）

## 5.1 一级高风险文件（优先净化）

1. src/agent/turn_coordinator.py
- 当前直接导入 tools.executor、tools.registry、tools.mcp、workspace 包内类型
- 目标改为仅导入：
  - from tools.tools_gateway import ToolsGateway
  - from workspace.workspace_gateway import WorkspaceGateway
  - from core_contracts.* import DTO/Errors

2. src/agent/agent.py
- 当前直接导入 tools.mcp/tools.registry + session/context/interaction/workspace 领域对象
- 目标改为 gateway-only 依赖

3. src/app/cli.py
- 当前直接依赖 interaction/openai_client/session 包导出
- 目标改为 interaction_gateway/openai_client_gateway/session_gateway

4. src/app/runtime_builder.py
- 当前直接依赖 agent/openai_client/session
- 目标改为 app_gateway 驱动下的 gateway 注入

5. src/interaction/slash_commands.py
- 当前直接依赖 context/session/tools 内部结构
- 目标改为 context_gateway/session_gateway/tools_gateway

## 5.2 二级清理文件

- src/agent/prompt_processor.py
- src/context/context_manager.py（与 agent.run_state 的跨层耦合）
- src/workspace/plugin_catalog.py
- src/workspace/policy_catalog.py
- src/workspace/__init__.py（过度导出）

---

## 6. 执行阶段拆分（Phase 2 用）

建议按“风险优先 + 影响面收敛”推进，每步严格单步闭环：

1. Step A：core_contracts 基础层
- 新增 gateway_errors.py + tools/session/workspace/context/planning/openai/app/agent 合同文件
- 先定义翻译契约，不改业务行为

2. Step B：tools Gateway 歼灭
- tool_gateway.py 重构为 tools_gateway.py
- 清空外部对 tools.executor/registry/mcp 的直接依赖

3. Step C：session Gateway 歼灭
- 建立 session_gateway.py
- 清空外部对 session snapshot/state 具体类的直接依赖

4. Step D：workspace Gateway 深化
- workspace_gateway 强化翻译层
- 清理 workspace/__init__.py 非 gateway 导出

5. Step E：context Gateway 歼灭
- 建立 context_gateway.py
- 清空 agent/interation 对 context 内部模型耦合

6. Step F：openai_client Gateway 歼灭
- 建立 openai_client_gateway.py
- 统一模型调用异常面

7. Step G：interaction Gateway 歼灭
- 建立 interaction_gateway.py
- app/agent 仅经 interaction_gateway 访问 UI/slash 能力

8. Step H：planning Gateway 歼灭
- 建立 planning_gateway.py
- 清理 planning 内部记录结构外泄

9. Step I：agent/app 顶层 Gateway 收口
- 增加 agent_gateway.py 与 app_gateway.py
- main 入口只依赖顶层 gateway + core_contracts

10. Step J：全局 import 纪律验收
- 扫描规则：跨模块只允许 module.xxx_gateway 或 core_contracts
- 测试与文档全量回归

---

## 7. 验收标准（本轮）

1. 跨模块导入零越权
- 不再出现 from tools.registry import ... 等导入
- 不再出现 from session import ...（非 gateway）

2. 领域边界零泄漏
- 外部模块拿不到领域内部异常与内部数据类
- 所有跨层对象均来自 core_contracts

3. Gateway 翻译职责落地
- 领域内部异常 -> core_contracts.gateway_errors
- 领域内部结构 -> core_contracts.*_contracts DTO

4. 回归通过
- 全量 unittest
- release gate
- 文档一致性测试

---

## 8. 首步执行建议

Phase 2 第一刀建议从 tools 领域开始（Step B），原因：

1. 当前违规导入最集中（agent/interaction/workspace 多点越权）
2. 异常与流式结果外泄最严重（ToolExecutionError/MCPTransportError/ToolStreamUpdate）
3. 一旦 tools 合同稳定，后续 session/context/workspace 改造会显著降风险

---

本计划已保存为 docs/architecture/GLOBAL_GATEWAY_REFACTOR_PLAN.md，作为第二阶段重构唯一执行蓝图。
