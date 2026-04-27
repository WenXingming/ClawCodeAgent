# Architecture

## 范围说明

- 本文档只描述根目录 `src/` 下当前生效的代码结构。
- 已排除工作区内嵌的 `claw-code-agent/` 目录。
- 根目录 `src/` 是源码根，不作为 `src` 包名参与导入；跨目录依赖统一使用顶层绝对导入。
- 本轮重构后，旧 `runtime/` 与预算相关旧 `context` 入口不再作为稳定导入路径保留。

## 当前结构

```text
src/
|- main.py
|- interface/
|  |- command_line_interface.py
|  '- slash_commands_interface.py
|- orchestration/
|  |- budget_context_orchestrator.py
|  '- local_agent.py
|- planning/
|  |- task_runtime.py
|  |- plan_runtime.py
|  '- workflow_runtime.py
|- extensions/
|  |- plugin_runtime.py
|  |- hook_policy_runtime.py
|  |- search_runtime.py
|  '- mcp/
|- budget/
|  '- budget_guard.py
|- context/
|  |- context_token_estimator.py
|  |- context_budget_evaluator.py
|  |- context_snipper.py
|  '- context_compactor.py
|- session/
|- tools/
|- openai_client/
'- core_contracts/
```

## 主视图

```mermaid
%%{init: {
    "theme": "default",
    "themeVariables": {
        "fontFamily": "Times New Roman",
        "fontSize": "20px"
    },
    "flowchart": {
        "curve": "basis",
        "nodeSpacing": 50,
        "rankSpacing": 70
    }
}}%%

graph TB
    accTitle: ClawCodeAgent Module Architecture and Dependencies
    accDescr: Clean layered architecture with package boundaries for control plane, orchestration, planning, extensions, budget and context.

    main(["🧭 main.py"])

    subgraph ControlPlane[interface package / CLI 与本地控制面]
        direction TB
        n_cli(["🧭 interface/command_line_interface.py"])
        n_slash(["⌨️ interface/slash_commands_interface.py"])
        style ControlPlane fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph Orchestration[orchestration package / 主循环编排]
        direction TB
        n_agent(["⚙️ orchestration/local_agent.py"])
        n_budget_context_orchestrator(["🧠 orchestration/budget_context_orchestrator.py"])
        style Orchestration fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph Extensions[extensions package / 工作区扩展能力]
        direction TB
        n_hook_policy(["🛡️ extensions/hook_policy_runtime.py"])
        n_plugin(["🧩 extensions/plugin_runtime.py"])
        n_search(["🔎 extensions/search_runtime.py"])
        n_mcp(["🛰️ extensions/mcp/runtime.py"])
        style Extensions fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph Planning[planning package / 任务与计划状态机]
        direction TB
        n_task(["📋 planning/task_runtime.py"])
        n_plan(["🗺️ planning/plan_runtime.py"])
        n_workflow(["🧭 planning/workflow_runtime.py"])
        style Planning fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph BudgetPkg[budget package / 执行预算闸门]
        direction TB
        n_budget_guard(["🛡️ budget/budget_guard.py"])
        style BudgetPkg fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph ContextPkg[context package / 上下文治理与预算]
        direction TB
        n_token_estimator(["🔢 context/context_token_estimator.py"])
        n_budget_evaluator(["📏 context/context_budget_evaluator.py"])
        n_snip(["✂️ context/context_snipper.py"])
        n_compact(["🗜️ context/context_compactor.py"])
        style ContextPkg fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph Tooling[tools package / 工具执行与安全]
        direction TB
        n_tools(["🛠️ tools/agent_tools.py"])
        n_bash_security(["🛡️ tools/bash_security.py"])
        style Tooling fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph SessionPkg[session package / 会话状态与持久化]
        direction TB
        n_session_state(["🧠 session/session_state.py"])
        n_session_store(["💽 session/session_store.py"])
        n_session_snapshot(["🧾 session/session_snapshot.py"])
        style SessionPkg fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph External[模型接入]
        direction TB
        n_openai_client(["☁️ openai_client/openai_client.py"])
        style External fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph Contracts[共享契约层]
        direction TB
        n_core_contracts(["📄 core_contracts/<br/>(config / protocol / usage / result)"])
        style Contracts fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    main --> n_cli

    n_cli --> n_agent
    n_cli --> n_session_store

    n_agent --> n_slash
    n_agent --> n_hook_policy
    n_agent --> n_plugin
    n_agent --> n_openai_client
    n_agent --> n_tools
    n_agent --> n_session_state
    n_agent --> n_session_store
    n_agent --> n_budget_context_orchestrator

    n_budget_context_orchestrator --> n_budget_guard
    n_budget_context_orchestrator --> n_budget_evaluator
    n_budget_context_orchestrator --> n_snip
    n_budget_context_orchestrator --> n_compact

    n_slash --> n_session_state
    n_slash --> n_tools
    n_slash --> n_budget_evaluator

    n_plan --> n_task
    n_workflow --> n_task
    n_hook_policy --> n_tools
    n_plugin --> n_tools
    n_tools --> n_bash_security

    n_session_store --> n_session_snapshot

    n_budget_evaluator --> n_token_estimator
    n_snip --> n_token_estimator
    n_compact --> n_token_estimator
    n_compact --> n_openai_client

    main -.-> n_core_contracts
    n_cli -.-> n_core_contracts
    n_slash -.-> n_core_contracts
    n_agent -.-> n_core_contracts
    n_hook_policy -.-> n_core_contracts
    n_plugin -.-> n_core_contracts
    n_search -.-> n_core_contracts
    n_mcp -.-> n_core_contracts
    n_task -.-> n_core_contracts
    n_plan -.-> n_core_contracts
    n_workflow -.-> n_core_contracts
    n_budget_guard -.-> n_core_contracts
    n_session_state -.-> n_core_contracts
    n_session_snapshot -.-> n_core_contracts
    n_tools -.-> n_core_contracts
    n_openai_client -.-> n_core_contracts

    style main fill:#343a40,color:#fff,stroke:#1d2124
    style n_cli fill:#6610f2,color:#fff,stroke:#520dc2
    style n_slash fill:#8a5cf6,color:#fff,stroke:#6f42c1
    style n_agent fill:#007bff,color:#fff,stroke:#0056b3
    style n_budget_context_orchestrator fill:#228be6,color:#fff,stroke:#1864ab
    style n_hook_policy fill:#198754,color:#fff,stroke:#146c43
    style n_plugin fill:#0d6efd,color:#fff,stroke:#0a58ca
    style n_search fill:#f76707,color:#fff,stroke:#d9480f
    style n_mcp fill:#1098ad,color:#fff,stroke:#0c8599
    style n_task fill:#20c997,color:#fff,stroke:#0f8f6b
    style n_plan fill:#ff922b,color:#fff,stroke:#d97706
    style n_workflow fill:#e8590c,color:#fff,stroke:#c2410c
    style n_budget_guard fill:#28a745,color:#fff,stroke:#1e7e34
    style n_budget_evaluator fill:#37b24d,color:#fff,stroke:#2b8a3e
    style n_token_estimator fill:#74b816,color:#fff,stroke:#5c940d
    style n_snip fill:#2f9e44,color:#fff,stroke:#1b5e20
    style n_compact fill:#1c7ed6,color:#fff,stroke:#1864ab
    style n_tools fill:#fd7e14,color:#fff,stroke:#d9480f
    style n_bash_security fill:#ffc107,color:#000,stroke:#d39e00
    style n_session_state fill:#17a2b8,color:#fff,stroke:#117a8b
    style n_session_snapshot fill:#17a2b8,color:#fff,stroke:#117a8b
    style n_session_store fill:#17a2b8,color:#fff,stroke:#117a8b
    style n_openai_client fill:#17a2b8,color:#fff,stroke:#117a8b
    style n_core_contracts fill:#6c757d,color:#fff,stroke:#495057
```

## 当前边界

- `orchestration/local_agent.py` (`LocalAgent`) 负责主循环编排职责：模型调用、工具回填、预算闸门、会话保存，通过 `BudgetContextOrchestrator` 调用上下文治理。
- `orchestration/budget_context_orchestrator.py` (`BudgetContextOrchestrator`) 统一编排 pre-model 阶段的 snip/compact/预算预检及 reactive compact 重试。
- `budget/` 负责 token 预算的对象模型和闸门逻辑：`TokenBudgetSnapshot`、统一估算器、预算投影器和运行时预算检查都集中在这里。
- `budget/` 只保留 `BudgetGuard`：集中管理主循环的五维执行限制（turns / model_calls / token / cost / tool_calls），是 orchestration 层的运行时闸门。
- `context/` 负责上下文治理与 token 预算能力：`ContextTokenEstimator` 提供 token 估算，`ContextBudgetEvaluator`（含 `ContextBudgetSnapshot`）提供预算投影，`ContextSnipper` 处理 tombstone 化，`ContextCompactor` 处理摘要压缩与 context-length 处理。
- `planning/` 负责工作区内本地状态机：任务、计划、工作流都各自持久化，但共享 `TaskRuntime` 作为最底层执行对象。
- `extensions/` 负责工作区扩展入口：插件、策略、搜索 provider、MCP server 都从工作区 `.claw/` manifest 或环境变量发现并对外提供独立 API。
- `interface/` 负责 CLI 和 slash 命令；`slash_commands_interface.py` 依赖预算投影和工具注册表，但不会触发模型调用。
- `main.py` 仍是很薄的装配入口，方便命令行调用和测试 patch。

这张图延续了原来的风格约束：容器框只表达包边界，实线保留主控制流和关键依赖，虚线收敛到共享契约层。与重构前相比，最大的变化不是调用方向，而是边界更清晰了：`runtime` 被拆成 `orchestration`、`planning`、`extensions`；token 估算与预算投影（`ContextTokenEstimator`、`ContextBudgetEvaluator`）归入 `context`，`budget` 只保留执行闸门 `BudgetGuard`，形成 `context` → `budget` → `orchestration` 的单向树状依赖。

## 测试镜像

```text
test/
|- test_main.py
|- test_main_chat.py
|- test_all.py
|- interface/
|- orchestration/
|- planning/
|- extensions/
|- budget/
|- context/
|- session/
|- tools/
|- openai_client/
'- core_contracts/
```

- `test/orchestration/` 对应主循环集成测试。
- `test/planning/` 对应 task/plan/workflow 状态机测试。
- `test/extensions/` 对应 plugin/policy/search/mcp 测试，并承接相关 patch 目标。
- `test/budget/` 对应预算快照、估算、评估与闸门测试。
- `test/budget/` 现在只包含 `test_budget_guard.py`（五维闸门测试）。
- `test/context/` 包含 `test_context_token_estimator.py`、`test_context_budget_evaluator.py`、`test_context_snipper.py` 与 `test_context_compactor.py`。
- `test/orchestration/` 包含 `test_budget_context_orchestrator.py` 与 `test_local_agent.py`。

## 推荐阅读顺序

1. 先看 `core_contracts/`，建立共享契约层边界。
2. 再看 `openai_client/openai_client.py` 与 `tools/agent_tools.py`，理解模型侧和工具侧的外部交互面。
3. 再看 `context/`（含 token 估算与预算投影）和 `budget/`（执行闸门），理解预算预检、上下文剪裁和摘要压缩的职责切分。
4. 再看 `planning/` 与 `extensions/`，理解工作区本地状态和外部扩展能力各自如何发现、持久化和暴露 API。
5. 最后看 `orchestration/local_agent.py`、`interface/command_line_interface.py` 和 `main.py`，理解这些能力如何被装配成完整入口。
