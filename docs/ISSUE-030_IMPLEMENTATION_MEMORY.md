# ISSUE-030 Implementation Memory - Step 2: context 域网关歼灭

## 目标

完成 context 域单步闭环：

1. 抽取跨模块契约到 core_contracts。
2. 建立 context 域统一网关并作为唯一跨域入口。
3. 精简调用方并清除 context <-> agent 的反向类型耦合。
4. 通过回归验证并输出导入扫描证据。

## 契约提炼

新增 [src/core_contracts/context_contracts.py](src/core_contracts/context_contracts.py)：

- BudgetProjection
- SessionMessageView（Protocol）
- ContextRunState（Protocol）
- PreModelBudgetGuard（Protocol）

其中 BudgetProjection 从 context 内部实现上提到 core_contracts，避免上层模块依赖 context 内部数据结构。

## 网关构筑

新增 [src/context/context_gateway.py](src/context/context_gateway.py)：

- ContextGateway.project_budget
- ContextGateway.run_pre_model_cycle
- ContextGateway.complete_with_reactive_compact

外部模块不再直接依赖 ContextManager，统一经由 ContextGateway 访问 context 域能力。

## 调用方精简

### 1) context -> agent 反向依赖清除

修改 [src/context/context_manager.py](src/context/context_manager.py)：

- 删除对 agent.run_limits.RunLimits 与 agent.run_state.AgentRunState 的导入。
- 改为依赖 core_contracts 协议类型：ContextRunState / PreModelBudgetGuard。

### 2) 上层跨域调用改走网关

- [src/agent/agent.py](src/agent/agent.py)
  - from context import ContextManager -> from context.context_gateway import ContextGateway
  - 内部字段改为 _context_gateway
  - 保留 context_manager 属性别名以兼容既有调用
- [src/agent/turn_coordinator.py](src/agent/turn_coordinator.py)
  - context_manager 字段类型改为 ContextGateway
- [src/interaction/slash_commands.py](src/interaction/slash_commands.py)
  - ContextManager -> ContextGateway

### 3) BudgetProjection 跨域类型收口

- [src/context/budget_projection.py](src/context/budget_projection.py)
  - 删除本地 BudgetProjection 定义，改为引入 core_contracts.context_contracts.BudgetProjection
- [src/agent/run_limits.py](src/agent/run_limits.py)
  - BudgetProjection 来源改为 core_contracts.context_contracts
- [src/agent/run_state.py](src/agent/run_state.py)
  - BudgetProjection 来源改为 core_contracts.context_contracts

## 验证

- context 回归：88 tests，OK
- agent 回归：64 tests，OK
- interaction 回归：32 tests，OK

严格导入扫描结果：

- TOTAL_VIOLATIONS=20
- CONTEXT_VIOLATIONS=0

说明：context 域违规已清零；剩余 20 处为其他域（session/interaction/agent/app 等）待后续分步处理。
