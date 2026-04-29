# ISSUE-027 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/core_contracts/gateway_errors.py` | 新建 | 统一 Gateway 异常契约（Permission/Validation/Transport/Runtime/NotFound） |
| `src/core_contracts/tools_contracts.py` | 新建 | 抽取 ToolDescriptor、ToolExecutionContext、ToolStreamUpdate 跨域契约 |
| `src/tools/tools_gateway.py` | 新建 | tools 领域唯一对外入口，封装执行/MCP 访问与异常归一化 |
| `src/tools/tool_gateway.py` | 删除 | 移除旧入口，避免双网关并存 |
| `src/agent/turn_coordinator.py` | 修改 | 去除 tools 内部导入与 mcp_runtime 直连，改为 ToolsGateway + core contracts |
| `src/agent/agent.py` | 修改 | 移除 MCPRuntime 构造耦合，改为 `tool_gateway.bind_workspace` |
| `src/workspace/plugin_catalog.py` | 修改 | LocalTool 全量替换为 ToolDescriptor，修复虚拟工具构建 NameError |
| `src/interaction/slash_commands.py` | 修改 | /tools 投影改为 ToolDescriptor.to_openai_tool，去除对 tools.registry 的直接依赖 |

## 关键设计决策

### 1. 先收敛契约，再收敛入口
先在 `core_contracts` 固化 tools DTO 与 gateway errors，再把调用方替换到新契约，避免调用方继续消费 tools 内部类型。

### 2. 破坏式清理旧入口
`src/tools/tool_gateway.py` 已删除，不保留兼容层，防止双入口继续扩散。

### 3. turn_coordinator 不搬家，只去泄漏
本期没有把 turn_coordinator 的编排逻辑“平移”到别的模块，而是只做边界净化：

- 删除 `tools.executor` / `tools.registry` / `tools.mcp` 直接导入
- 统一通过 `ToolsGateway` 持有的 MCP runtime 调用能力
- 错误契约改为 `GatewayValidationError` / `GatewayPermissionError`

### 4. 权限错误语义保持稳定
在 `_execute_mcp_tool_call` 中单独透传 `GatewayPermissionError`，避免被统一包装成 validation error，保持 `permission_denied` 语义与既有测试一致。

## 删除清单

- 删除 `src/tools/tool_gateway.py`（legacy 入口）
- 删除 `TurnCoordinator` 中 `mcp_runtime` dataclass 字段
- 删除调用方对 `LocalTool`、`ToolExecutionError`、`ToolPermissionError`、`MCPTransportError`、`MCPRuntime` 的跨域依赖

## 测试覆盖与结果

定向回归命令（均在仓库根目录执行，`PYTHONPATH=src`）：

- `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/tools -v` → 26/26 OK
- `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/interaction -v` → 32/32 OK
- `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/agent -v` → 64/64 OK
- `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -v` → 26/26 OK

合计：148 个定向测试全部通过。

## 风险与后续

- 当前 `turn_coordinator` 已完成边界收敛，但仍保留较多工具参数解析辅助函数；下一阶段可在 agent 域内部继续做“同域瘦身”，不跨域搬家。
- 本期未执行全量 `test` 回归，仅完成 tools/interaction/agent/extensions 四个与 Step B 强相关分组。