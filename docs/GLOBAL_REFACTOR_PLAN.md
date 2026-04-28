# ClawCodeAgent 全局重构蓝图

> 本文件是后续重构推进的唯一权威凭证。
>
> 从本文件生效起，旧的 `docs/FINAL_ARCHITECTURE_PLAN.md` 仅作为历史归档，不再作为执行依据。

## 1. 文档定位

本次重构是一次破坏式、全局性、长期推进的系统重建。

目标不是修补现有结构，而是主动删除错误设计、重建领域边界、消除历史包袱，让系统达到以下状态：

1. 目录边界极度清晰。
2. 类的职责极度单一。
3. 包的导出面极度克制。
4. 编排器只编排，不再夹带业务与转换细节。
5. 任何后续开发都能顺着结构自然落位，而不是继续堆到巨石文件里。

## 2. 最高原则

### 2.1 破坏式重构优先

只要旧设计已经妨碍整体结构，就直接删除，不保留兼容层，不保留转发层，不保留“暂时先留着”的历史包袱。

### 2.2 高内聚、低耦合

每个包只负责一个领域。每个类只负责一个清晰角色。禁止横向穿透式依赖。

### 2.3 Facade 是角色，不是强制命名

Facade 是一种架构职责，不要求所有公开入口都命名为 `Facade`。

允许使用更自然的名字，例如：

1. `Agent`
2. `ContextManager`
3. `ToolGateway`
4. `WorkspaceGateway`
5. `SessionManager`
6. `PlanningService`
7. `QueryService`

但它们都必须承担 Facade 的职责：

1. 作为该领域唯一公开入口。
2. 屏蔽内部实现细节。
3. 对外只暴露极少量语义明确的方法。

### 2.4 私有性优先通过类与包边界表达，不通过文件名前缀表达

本重构不采用 `_chat_loop.py`、`_turn_engine.py` 这种下划线模块文件名。

隐蔽性通过以下方式表达：

1. 类的 `_private_method` 与 `_private_attr`
2. 包的 `__init__.py` 限制导出面
3. 约定“非 `__init__.py` 导出的模块都不是公共 API”

结论：

1. 允许 `__init__.py`
2. 允许类内部 `_private` 成员
3. 不再新增普通下划线模块文件

### 2.5 统一状态对象优先

Agent 主循环中的可变运行状态必须统一收拢到 `AgentRunState`，禁止继续通过大量松散变量和长参数列表在多个模块间传递。

### 2.6 配置与状态分离

`AgentRunState` 负责“运行时会变化的状态”。

静态配置契约只负责“运行开始前已知的约束”。

二者绝不混合。

### 2.7 深度优先阅读顺序

每个类中的方法顺序必须满足：

1. 公共方法在上
2. 被公共方法调用的私有方法紧随其后
3. 阅读时可沿调用链自然向下追踪

### 2.8 每一步都必须闭环

每个 Step 都必须同时交付：

1. 代码
2. 测试
3. 文档
4. 验证结果

做完必须暂停，等待人工确认。

## 3. 当前系统的主要病灶

### 3.1 主编排巨石

`src/orchestration/local_agent.py` 当前同时承担：

1. run / resume 主流程
2. prompt 预处理
3. slash 分流
4. 动态工具注册
5. MCP 能力物化
6. 工具执行与流式输出
7. delegate_agent 编排
8. 事件记录与结果组装

这已经不是高内聚对象，而是总装车间。

### 3.2 控制面巨石

`src/interaction/command_line_interaction.py` 当前同时承担：

1. 参数解析
2. 配置构建
3. 校验与覆盖
4. agent 实例装配
5. chat loop
6. resume 路径
7. 渲染与退出处理

这导致 CLI 层同时知道过多内部细节。

### 3.3 契约大杂烩

`src/core_contracts/config.py` 把以下东西混装进一个文件甚至一个对象体系：

1. 模型配置
2. 预算配置
3. 权限配置
4. 结构化输出配置
5. 工作区范围配置
6. 执行策略配置
7. 会话路径配置

这直接阻碍后续拆分。

### 3.4 领域边界泄漏

agent 当前直接知道：

1. plugin
2. policy
3. search
4. worktree
5. MCP runtime
6. tool context 细节

这说明领域边界没有立起来。

### 3.5 隐式公共接口泛滥

当前大量模块直接相互 import 内部实现文件，缺少显式导出层，导致“文件路径”在事实上成了 API。

## 4. 必删旧设计

以下对象被明确判定为必须删除：

1. `AgentRuntimeConfig`
2. `src/core_contracts/config.py`
3. `src/orchestration/local_agent.py`
4. `src/interaction/command_line_interaction.py`
5. `src/openai_client/openai_client.py` 作为旧目录边界的一部分
6. `src/budget/budget_guard.py` 作为独立包边界
7. `src/core_contracts/_coerce.py` 这种下划线模块命名
8. 任何兼容旧 import 的转发层

### 4.1 关于 AgentRuntimeConfig 的最终结论

`AgentRuntimeConfig` 必须删除，但不是由 `AgentRunState` 直接替代。

原因如下：

1. `AgentRuntimeConfig` 是静态配置的混装对象。
2. `AgentRunState` 是运行中的动态状态对象。
3. 两者职责不同，不能互相冒充。

正确做法是：

1. 删除 `AgentRuntimeConfig`
2. 新建更小的静态契约对象
3. 同时引入 `AgentRunState`

## 5. 新架构中的关键对象

### 5.1 统一动态状态对象

`AgentRunState` 定义在 `src/agent/run_state.py`。

它至少包含：

1. `session_id`
2. `resumed_from_session_id`
3. `session_state`
4. `turn_index`
5. `turns_completed`
6. `model_call_count`
7. `tool_call_count`
8. `usage_delta`
9. `usage_total`
10. `cost_baseline`
11. `final_output`
12. `stop_reason`
13. `events`
14. `active_tools`
15. `model_tools`
16. `budget_snapshot`

### 5.2 替换后的静态契约

`src/core_contracts/config.py` 删除后，拆成以下契约文件：

1. `core_contracts/budget.py`
2. `core_contracts/model.py`
3. `core_contracts/permissions.py`
4. `core_contracts/runtime_policy.py`
5. `core_contracts/protocol.py`
6. `core_contracts/run_result.py`
7. `core_contracts/token_usage.py`

其中新增的小契约建议如下：

1. `WorkspaceScope`
2. `ExecutionPolicy`
3. `ContextPolicy`
4. `SessionPaths`
5. `StructuredOutputSpec`
6. `ToolPermissionPolicy`

## 6. 重构前目录树

```text
src/
├─ main.py
├─ budget/
│  └─ budget_guard.py
├─ context/
│  ├─ context_token_estimator.py
│  ├─ context_token_budget_evaluator.py
│  ├─ context_snipper.py
│  └─ context_compactor.py
├─ core_contracts/
│  ├─ _coerce.py
│  ├─ config.py
│  ├─ model_pricing.py
│  ├─ protocol.py
│  ├─ run_result.py
│  └─ token_usage.py
├─ extensions/
│  ├─ hook_policy_runtime.py
│  ├─ plugin_runtime.py
│  ├─ search_runtime.py
│  └─ worktree_runtime.py
├─ interaction/
│  ├─ command_line_interaction.py
│  ├─ slash_commands.py
│  ├─ slash_render.py
│  └─ ...
├─ openai_client/
│  └─ openai_client.py
├─ orchestration/
│  ├─ local_agent.py
│  ├─ query_engine.py
│  ├─ budget_context_orchestrator.py
│  └─ agent_manager.py
├─ planning/
│  ├─ task_runtime.py
│  ├─ plan_runtime.py
│  └─ workflow_runtime.py
├─ session/
│  ├─ session_state.py
│  ├─ session_snapshot.py
│  └─ session_store.py
└─ tools/
   ├─ local_tools.py
   ├─ mcp_runtime.py
   ├─ mcp_manifest_loader.py
   ├─ mcp_transport.py
   ├─ mcp_models.py
   └─ bash_security.py

test/
├─ budget/
├─ context/
├─ core_contracts/
├─ extensions/
├─ interaction/
├─ openai_client/
├─ orchestration/
├─ planning/
├─ session/
├─ tools/
├─ test_all.py
├─ test_main.py
└─ test_main_chat.py
```

## 7. 重构后目标目录树

```text
src/
├─ main.py
├─ app/
│  ├─ __init__.py
│  ├─ cli.py
│  ├─ chat_loop.py
│  ├─ query_service.py
│  └─ runtime_builder.py
├─ agent/
│  ├─ __init__.py
│  ├─ agent.py
│  ├─ run_state.py
│  ├─ prompt_processor.py
│  ├─ turn_coordinator.py
│  ├─ run_limits.py
│  ├─ delegation_service.py
│  └─ result_factory.py
├─ context/
│  ├─ __init__.py
│  ├─ context_manager.py
│  ├─ budget_projection.py
│  ├─ snipper.py
│  └─ compactor.py
├─ tools/
│  ├─ __init__.py
│  ├─ tool_gateway.py
│  ├─ registry.py
│  ├─ executor.py
│  ├─ local/
│  │  ├─ __init__.py
│  │  ├─ filesystem_tools.py
│  │  └─ shell_tools.py
│  └─ mcp/
│     ├─ __init__.py
│     ├─ mcp_gateway.py
│     ├─ runtime.py
│     ├─ manifest_loader.py
│     ├─ transport.py
│     ├─ models.py
│     └─ renderer.py
├─ workspace/
│  ├─ __init__.py
│  ├─ workspace_gateway.py
│  ├─ plugin_catalog.py
│  ├─ policy_catalog.py
│  ├─ search_service.py
│  └─ worktree_service.py
├─ session/
│  ├─ __init__.py
│  ├─ session_manager.py
│  ├─ state.py
│  ├─ snapshot.py
│  └─ store.py
├─ planning/
│  ├─ __init__.py
│  ├─ planning_service.py
│  ├─ task_runtime.py
│  ├─ plan_runtime.py
│  └─ workflow_runtime.py
├─ model/
│  ├─ __init__.py
│  └─ openai_client.py
└─ core_contracts/
   ├─ __init__.py
   ├─ budget.py
   ├─ model.py
   ├─ permissions.py
   ├─ runtime_policy.py
   ├─ protocol.py
   ├─ run_result.py
   ├─ token_usage.py
   ├─ model_pricing.py
   └─ coercion.py

test/
├─ app/
├─ agent/
├─ context/
├─ core_contracts/
├─ model/
├─ planning/
├─ session/
├─ tools/
├─ workspace/
├─ test_all.py
├─ test_main.py
└─ test_main_chat.py

docs/
├─ GLOBAL_REFACTOR_PLAN.md
├─ REFAC-STEP-01_IMPLEMENTATION_MEMORY.md
├─ REFAC-STEP-02_IMPLEMENTATION_MEMORY.md
├─ ...
└─ ISSUE-*.md  (历史归档，不再作为执行蓝图)
```

## 8. 领域职责与唯一公共入口

### 8.1 app

职责：控制面与上层交互。

公开入口：

1. `AppCLI`
2. `QueryService`

### 8.2 agent

职责：核心运行编排。

公开入口：

1. `Agent`
2. `AgentRunState`

### 8.3 context

职责：上下文预算投影、snip、compact、overflow 恢复。

公开入口：

1. `ContextManager`

### 8.4 tools

职责：本地工具注册、执行、工具 schema 构建、MCP 代理。

公开入口：

1. `ToolGateway`
2. `McpGateway`

### 8.5 workspace

职责：插件、策略、搜索、worktree 等工作区级能力收口。

公开入口：

1. `WorkspaceGateway`

### 8.6 session

职责：会话内存态与持久化管理。

公开入口：

1. `SessionManager`

### 8.7 planning

职责：任务、计划、工作流状态机。

公开入口：

1. `PlanningService`

### 8.8 model

职责：模型调用适配。

公开入口：

1. `OpenAIClient`

## 9. 旧结构到新结构的映射

| 旧模块 | 新归属 | 处理方式 |
|--------|--------|----------|
| `interaction/command_line_interaction.py` | `app/cli.py` + `app/chat_loop.py` + `app/runtime_builder.py` | 直接拆分并删除旧文件 |
| `orchestration/local_agent.py` | `agent/agent.py` + `agent/prompt_processor.py` + `agent/turn_coordinator.py` + `agent/delegation_service.py` + `agent/result_factory.py` | 直接拆分并删除旧文件 |
| `orchestration/query_engine.py` | `app/query_service.py` | 迁移并重写 |
| `orchestration/budget_context_orchestrator.py` | `context/context_manager.py` | 吸收后删除 |
| `orchestration/agent_manager.py` | `agent/delegation_service.py` | 吸收后删除 |
| `budget/budget_guard.py` | `agent/run_limits.py` | 合并后删除旧包 |
| `openai_client/openai_client.py` | `model/openai_client.py` | 移动并保留类名 |
| `extensions/plugin_runtime.py` | `workspace/plugin_catalog.py` | 重写并迁移 |
| `extensions/hook_policy_runtime.py` | `workspace/policy_catalog.py` | 重写并迁移 |
| `extensions/search_runtime.py` | `workspace/search_service.py` | 重写并迁移 |
| `extensions/worktree_runtime.py` | `workspace/worktree_service.py` | 重写并迁移 |
| `tools/local_tools.py` | `tools/tool_gateway.py` + `tools/registry.py` + `tools/executor.py` + `tools/local/*` | 拆分 |
| `tools/mcp_*` | `tools/mcp/*` | 收拢 |
| `core_contracts/config.py` | `core_contracts/*` 多文件 | 直接拆碎 |
| `core_contracts/_coerce.py` | `core_contracts/coercion.py` | 重命名 |

## 10. 全局重构实施 Checklist

### Step 1：拆毁契约大杂烩

目标：删除 `core_contracts/config.py` 和 `AgentRuntimeConfig`，建立新的静态契约分层。

执行内容：

1. 新建 `budget.py`、`model.py`、`permissions.py`、`runtime_policy.py`
2. 保留 `protocol.py`、`run_result.py`、`token_usage.py`，但修正 import 边界
3. 把 `_coerce.py` 重命名为 `coercion.py`
4. 彻底删除 `AgentRuntimeConfig`
5. 全量替换所有相关 import
6. 同步重写契约测试与序列化测试

必须删除：

1. `src/core_contracts/config.py`
2. `src/core_contracts/_coerce.py`

完成标准：

1. 仓库中不再出现 `AgentRuntimeConfig`
2. 仓库中不再出现 `core_contracts.config`
3. 契约测试全部通过

### Step 2：重建 session 与静态配置装配路径

目标：让 session、CLI、resume 体系全部切到新的静态契约上。

执行内容：

1. 重写 `session/snapshot.py` 的序列化结构
2. 重写 `session/store.py` 的恢复路径
3. 让 CLI 构建逻辑改为组装 `WorkspaceScope`、`ExecutionPolicy`、`ContextPolicy`、`SessionPaths`
4. 删除所有依赖旧 runtime config 的快照字段与恢复逻辑
5. 重写 `test/session/*` 与 `test/test_main*.py` 中相关用例

完成标准：

1. resume 不再依赖旧 runtime config 对象
2. 会话落盘结构只依赖新契约
3. session 与 main 相关测试通过

### Step 3：建立 `AgentRunState`

目标：统一 agent 主流程中的动态状态。

执行内容：

1. 新建 `agent/run_state.py`
2. 把 turn、usage、events、budget snapshot、tool registry 等状态统一收口
3. 精简 `AgentSessionState`，让它退回消息与 transcript 容器角色
4. 移除编排器间长参数链

完成标准：

1. agent 主流程只围绕一个动态状态对象推进
2. 不再使用长参数列表传递运行态变量

### Step 4：重建工具层

目标：让工具执行成为独立子系统。

执行内容：

1. 新建 `tools/tool_gateway.py`
2. 新建 `tools/registry.py`
3. 新建 `tools/executor.py`
4. 把本地文件工具拆进 `tools/local/filesystem_tools.py`
5. 把 shell 工具拆进 `tools/local/shell_tools.py`
6. 把 MCP 全量迁移到 `tools/mcp/`
7. 删除 `tools/local_tools.py` 的混装结构

完成标准：

1. agent 不再直接知道 tool context 细节
2. bash 流式输出细节完全下沉到工具层
3. 工具测试迁移并通过

### Step 5：重建 workspace 层

目标：把插件、策略、搜索、worktree 统一收口到工作区领域。

执行内容：

1. 新建 `workspace/workspace_gateway.py`
2. 新建 `plugin_catalog.py`
3. 新建 `policy_catalog.py`
4. 新建 `search_service.py`
5. 新建 `worktree_service.py`
6. agent 只通过 `WorkspaceGateway` 获取 hooks、block 决策和工作区能力

完成标准：

1. agent 不再直接 import `plugin_runtime`、`hook_policy_runtime`、`search_runtime`、`worktree_runtime`
2. `extensions/` 失去存在必要性

### Step 6：重建 context 层

目标：把上下文治理从“若干工具类”改成一个专业领域。

执行内容：

1. 新建 `context/context_manager.py`
2. 把 `context_token_budget_evaluator.py` 重组为 `budget_projection.py`
3. 把 `context_snipper.py` 重组为 `snipper.py`
4. 把 `context_compactor.py` 重组为 `compactor.py`
5. 删除 `budget_context_orchestrator.py`

完成标准：

1. agent 只调用 `ContextManager`
2. context 层对外只暴露单一入口
3. 上下文相关测试迁移并通过

### Step 7：重建 agent 核心

目标：拆毁 `local_agent.py` 巨石。

执行内容：

1. 新建 `agent/agent.py`
2. 新建 `prompt_processor.py`
3. 新建 `turn_coordinator.py`
4. 新建 `run_limits.py`
5. 新建 `delegation_service.py`
6. 新建 `result_factory.py`
7. 把 `BudgetGuard` 逻辑吸收到 `run_limits.py`
8. 把 `AgentManager` 逻辑吸收到 `delegation_service.py`
9. 删除 `src/orchestration/local_agent.py`
10. 删除 `src/budget/budget_guard.py`
11. 删除 `src/orchestration/agent_manager.py`

完成标准：

1. `Agent` 对外只暴露 `run()`、`resume()`
2. 主编排文件不再承担工具、搜索、MCP、策略等细节
3. agent 相关测试迁移并通过

### Step 8：重建 app 控制面

目标：把 CLI、query、chat loop 从旧 interaction 巨石中拆出来。

执行内容：

1. 新建 `app/cli.py`
2. 新建 `app/chat_loop.py`
3. 新建 `app/runtime_builder.py`
4. 新建 `app/query_service.py`
5. 把旧 `QueryEngine` 迁入 `query_service.py`
6. 删除 `src/interaction/command_line_interaction.py`
7. 删除 `src/orchestration/query_engine.py`

完成标准：

1. `main.py` 仍然极薄
2. CLI 不再知道 agent 内部实现细节
3. `test_main.py` 与 `test_main_chat.py` 通过

### Step 9：收口 session 与 planning 的公开边界

目标：为这两个相对稳定的领域补上唯一公开入口。

执行内容：

1. 新建 `session/session_manager.py`
2. 新建 `planning/planning_service.py`
3. 包级 `__init__.py` 只导出公开入口与稳定契约
4. 清理外层对内部实现的直接依赖

完成标准：

1. 外层模块只依赖 `SessionManager` 和 `PlanningService`
2. 直接穿透 `store.py`、`task_runtime.py` 的调用被收口

### Step 10：物理删除旧目录并镜像测试结构

目标：完成真正意义上的旧架构死亡。

执行内容：

1. 删除 `orchestration/`
2. 删除 `interaction/`
3. 删除 `extensions/`
4. 删除 `openai_client/`
5. 删除 `budget/`
6. 调整 `test/` 目录镜像新的 `src/` 结构
7. 保留 `test/test_all.py` 非包化递归装载机制

完成标准：

1. 仓库中不存在旧目录依赖
2. 测试目录与生产目录同构
3. 不存在兼容 wrapper 与旧 import shim

### Step 11：文档收口与发布级验证

目标：让文档、目录、测试命令三者完全一致。

执行内容：

1. 后续每一步落地时分别生成 `docs/REFAC-STEP-XX_IMPLEMENTATION_MEMORY.md`
2. 更新 README、Architecture、Release Gate 文档
3. 执行全量回归与 release gate

完成标准：

1. 文档只描述新结构，不再混入旧路线图
2. 全量测试通过
3. release gate 通过

## 11. 执行闭环规则

后续严格遵循：

1. 一次只推进一个 Step
2. 当前 Step 必须同时修改代码、测试、文档
3. 当前 Step 完成后必须暂停
4. 只有在人工确认后才进入下一步

## 12. 每一步的固定交付模板

每个 Step 都必须提供：

1. 删除了哪些旧设计
2. 新增了哪些类与模块
3. 新的公开接口是什么
4. 改写了哪些测试
5. 新增了哪份实现文档
6. 跑了哪些验证命令

## 13. 下一步起点

确认后，从 Step 1 开始。

第一刀明确为：

1. 删除 `src/core_contracts/config.py`
2. 删除 `AgentRuntimeConfig`
3. 拆分静态配置契约
4. 重写相关测试与会话快照结构

这一步做完后必须暂停，等待人工确认是否继续下一步。