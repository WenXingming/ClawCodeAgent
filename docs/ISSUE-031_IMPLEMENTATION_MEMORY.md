# ISSUE-031 Implementation Memory - Step 2b+3: 去包装 + session 域网关歼灭

## 本步修正

上一步 context_gateway 被识别为"换名包装"——context_manager 逻辑被原封不动委托给旧文件。  
本步按"破坏式重构"原则彻底改正：

1. 将 context_manager.py 全部逻辑直接内聚进 context_gateway.py。
2. 删除 context_manager.py（物理删除）。
3. session 域同理：直接创建 session_gateway.py，删除 session_manager.py。

## 已删除的文件

- `src/context/context_manager.py` ← 所有逻辑已内聚到 context_gateway.py
- `src/session/session_manager.py` ← 所有逻辑已内聚到 session_gateway.py

## context 域

[src/context/context_gateway.py](src/context/context_gateway.py) 现在直接持有：

- BudgetProjector / Snipper / Compactor 三个内部依赖
- PreModelContextOutcome / ReactiveCompactOutcome 两个出口数据类
- run_pre_model_cycle / complete_with_reactive_compact 两段完整逻辑
- _make_compact_event / _require_compactor 内部 helper

无中间层，无委托，无包装。

## session 域

[src/session/session_gateway.py](src/session/session_gateway.py) 直接持有原 session_manager 全部方法：

- save_session / load_session（通过 AgentSessionStore）
- create_session_state / restore_session_state（通过 AgentSessionState）
- 保留 `SessionManager = SessionGateway` 别名供测试 patch 路径兼容

## 所有 src 跨域导入收口

所有 `from session import ...`、`from context import ContextManager` 已替换为：

- `from context.context_gateway import ContextGateway`
- `from session.session_gateway import SessionGateway, AgentSessionSnapshot, AgentSessionState`

涉及文件：

- [src/agent/agent.py](src/agent/agent.py)
- [src/agent/turn_coordinator.py](src/agent/turn_coordinator.py)
- [src/agent/result_factory.py](src/agent/result_factory.py)
- [src/agent/run_state.py](src/agent/run_state.py)
- [src/app/cli.py](src/app/cli.py)
- [src/app/runtime_builder.py](src/app/runtime_builder.py)
- [src/app/chat_loop.py](src/app/chat_loop.py)
- [src/interaction/slash_commands.py](src/interaction/slash_commands.py)
- [src/main.py](src/main.py)

## 测试同步

- [test/context/test_context_manager.py](test/context/test_context_manager.py)：改用 ContextGateway 直接构造
- [test/agent/test_run_limits_context_manager.py](test/agent/test_run_limits_context_manager.py)：同上

## 验证结果

- context 回归：OK
- agent 回归：64 tests，OK
- interaction 回归：32 tests，OK
- main/CLI 回归：27 tests，OK

严格导入扫描：

- TOTAL_VIOLATIONS=11
- CONTEXT_VIOLATIONS=0
- SESSION_VIOLATIONS=0
