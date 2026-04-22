# Architecture

## 范围说明

- 本文档只分析根目录 `src` 文件夹下的 Python 文件。
- 已排除 `claw-code-agent` 文件夹下的所有文件。
- 图中每个节点代表一个文件，边 `A --> B` 表示 `A` 通过 `import/from import` 引用了 `B`。

## 文件引用关系（src）

```mermaid
%%{init: {
    "theme": "default",
    "themeVariables": {
        "fontFamily": "Times New Roman",
        "fontSize": "20px"
    },
    "flowchart": {
        "curve": "basis" 
    }
}}%%

graph TD
    %% 全局样式设置
    accTitle: Agent System Architecture
    accDescr: A diagram showing the module dependencies of an AI agent system.

    %% 模块分组
    subgraph Infrastructure [基础设施 / 协议层]
        n_contract(["📄 contract_types.py<br/>(Models & Protocols)"])
        style Infrastructure fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph Security [安全审计]
        n_bash_security(["🛡️ bash_security.py"])
        style Security fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph External [外部集成]
        n_openai(["☁️ openai_client.py"])
        style External fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    subgraph Core [逻辑核心]
        n_runtime(["⚙️ agent_runtime.py"])
        n_session_pkg(["💾 session/<br/>state.py + contracts.py + store.py"])
        n_tools(["🛠️ agent_tools.py"])
        style Core fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    end

    %% 业务逻辑依赖 (实线)
    n_runtime --> n_session_pkg
    n_runtime --> n_tools
    n_runtime --> n_openai
    
    n_tools --> n_bash_security
    
    %% 数据协议依赖 (虚线，避免视觉干扰)
    n_session_pkg -.-> n_contract
    n_tools -.-> n_contract
    n_openai -.-> n_contract
    n_runtime -.-> n_contract

    %% 自定义节点颜色 (继承你原有的配色)
    style n_runtime fill:#007bff,color:#fff,stroke:#0056b3
    style n_contract fill:#6c757d,color:#fff
    style n_bash_security fill:#ffc107,color:#000
```

## 快速阅读建议

- 先看 `contract_types.py`：它是核心契约层，被多个模块依赖。
- 再看 `openai_client.py` 与 `agent_tools.py`：分别是模型调用层和工具执行层。
- 然后看 `session/` 子包：`state.py` 维护内存态消息，`contracts.py` 定义落盘契约，`store.py` 负责 session 落盘与恢复。
- 最后看 `agent_runtime.py`：它把契约、模型、工具与持久化串成最小闭环。
- `__init__.py` 主要负责对外导出，不承载业务逻辑。
