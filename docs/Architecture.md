# Architecture

## 范围说明

- 本文档只描述根目录 `src/` 下当前生效的代码结构。
- 已排除工作区内嵌的 `claw-code-agent/` 目录。
- 根目录 `src/` 现在是源码根，不再作为 `src` 包名参与导入；跨目录依赖统一使用顶层绝对导入，如 `from core_contracts.config import AgentRuntimeConfig`。
- 本文档不再重复 project tree 的逐文件结构；重点是先讲清包/模块容器关系，再补少量真正影响理解的跨层依赖。

## 主视图：项目模块架构和依赖关系

逐文件结构请直接看 project tree；下面这张主图把模块分组和关键依赖放在一起，既保留容器关系，也避免把纯转发节点画进去。

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
    accDescr: Clean layered architecture with minimized edge crossings.

    %% ================= Top =================
    main(["🧭 main.py"])

    %% ================= Runtime =================
    subgraph Runtime [runtime package / 主循环编排]
        direction TB
        n_runtime(["⚙️ runtime/agent_runtime.py"])
        style Runtime fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    %% ================= Core Infra（关键：上移） =================
    subgraph External [模型接入]
        direction TB
        n_openai_client(["☁️ openai_client/openai_client.py"])
        style External fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    %% ================= Context（左） =================
    subgraph ContextPkg [context package]
        direction TB
        n_budget_guard(["🛡️ budget_guard.py"])
        n_snip(["✂️ snip.py"])
        n_compact(["🗜️ compact.py"])
        n_token_budget(["🔢 token_budget.py"])
        style ContextPkg fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    %% ================= Tooling（右） =================
    subgraph Tooling [tools package / 工具执行与安全]
        direction TB
        n_tools(["🛠️ tools/agent_tools.py"])
        n_bash_security(["🛡️ tools/bash_security.py"])
        style Tooling fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    %% ================= Session（下） =================
    subgraph SessionPkg [session package]
        direction TB
        n_session_state(["🧠 session_state.py"])
        n_session_store(["💽 session_store.py"])
        n_session_contracts(["🧾 session_contracts.py"])
        style SessionPkg fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    %% ================= Contracts（最底） =================
    subgraph Contracts [共享契约层]
        direction TB
        n_core_contracts(["📄 core_contracts/<br/>(config / protocol / usage / result )"])
        style Contracts fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    %% ================= 主链路 =================
    main --> n_runtime

    n_runtime --> n_openai_client
    n_runtime --> n_tools
    n_runtime --> n_session_state
    n_runtime --> n_session_store
    n_runtime --> n_budget_guard
    n_runtime --> n_snip
    n_runtime --> n_compact

    %% ================= 工具 =================
    n_tools --> n_bash_security

    %% ================= Session =================
    n_session_store --> n_session_contracts

    %% ================= Context =================
    n_budget_guard --> n_token_budget
    n_snip --> n_token_budget
    n_compact --> n_token_budget

    %% ⭐ 关键：缩短跨线（现在是“垂直短边”）
    n_compact --> n_openai_client

    %% ================= Contracts（全部下沉收敛） =================
    main -.-> n_core_contracts
    n_runtime -.-> n_core_contracts
    n_tools -.-> n_core_contracts
    n_openai_client -.-> n_core_contracts
    n_session_state -.-> n_core_contracts
    n_session_contracts -.-> n_core_contracts
    n_budget_guard -.-> n_core_contracts
    n_compact -.-> n_core_contracts

    %% ================= 样式 =================
    style main fill:#343a40,color:#fff,stroke:#1d2124
    style n_runtime fill:#007bff,color:#fff,stroke:#0056b3
    style n_core_contracts fill:#6c757d,color:#fff,stroke:#495057
    style n_openai_client fill:#17a2b8,color:#fff,stroke:#117a8b
    style n_bash_security fill:#ffc107,color:#000,stroke:#d39e00
    style n_session_state fill:#17a2b8,color:#fff,stroke:#117a8b
    style n_session_contracts fill:#17a2b8,color:#fff,stroke:#117a8b
    style n_session_store fill:#17a2b8,color:#fff,stroke:#117a8b
    style n_token_budget fill:#28a745,color:#fff,stroke:#1e7e34
    style n_budget_guard fill:#28a745,color:#fff,stroke:#1e7e34
    style n_snip fill:#28a745,color:#fff,stroke:#1e7e34
    style n_compact fill:#28a745,color:#fff,stroke:#1e7e34
    style n_tools fill:#fd7e14,color:#fff,stroke:#d9480f
```

这张图把模块分组和关键依赖放在一起：分组框表示模块归属，实线保留主控制流和关键调用链，虚线只表示对 `core_contracts/` 这个共享底座的契约依赖。当前最重要的三个事实是：

- `core_contracts/` 已经成为共享底座，跨模块 dataclass、JSON 协议和配置解析都从这里下沉出去。
- `openai_client/` 现在是源码根下的命名空间目录，不再依赖 `__init__.py`；具体 HTTP 与 SSE 解析仍然集中在 `openai_client/openai_client.py`。
- `context/compact.py` 是少数刻意允许跨层调用客户端的模块，因为它需要主动发起摘要压缩模型请求。

## 推荐阅读顺序

1. 先看 `core_contracts/`，建立共享契约层与配置/协议对象的边界感。
2. 再看 `openai_client/openai_client.py` 与 `tools/agent_tools.py`，理解模型侧和工具侧两个外部交互面。
3. 再看 `session/` 与 `context/`，理解状态恢复、预算治理、snip、compact 的局部职责。
4. 最后看 `runtime/agent_runtime.py` 与 `main.py`，把编排主循环和 CLI 入口串起来。