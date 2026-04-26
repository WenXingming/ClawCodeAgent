# ISSUE-021 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/extensions/mcp_runtime.py` | 新建 | 实现 MCP manifest 发现、本地资源读取和 stdio transport 资源/工具调用 |
| `test/extensions/test_mcp_runtime.py` | 新建 | 覆盖资源读取、工具调用、无效 server 错误追踪 |
| `README.md` | 修改 | 补充 MCP Runtime 的 manifest 路径、stdio 能力与示例 |
| `docs/Architecture.md` | 修改 | 把 mcp runtime 写回架构视图与运行时说明 |

## 关键设计决策

### 1. MCP Runtime 保持独立，不直接接 agent 主循环
ISSUE-021 的目标是 MCP 资源/工具发现与 stdio transport 调用，不是立即把 MCP runtime 注入当前工具执行链。因此本期把 `extensions/mcp_runtime.py` 保持为独立模块，后续再由控制面或工具链相关 issue 接入。

### 2. manifest 统一走 `.claw/` 目录
当前实现使用：

- `.claw/mcp.json`
- `.claw/mcp/*.json`

这样与 plugin / policy / task / plan / workflow / search 的工作区本地运行时保持一致，MCP profile 和本地资源都可以随仓库一起管理。

### 3. 同时支持 manifest 本地资源与 stdio server profile
当前 manifest 可以同时描述两类东西：

- 本地 `resources`
- transport-backed `mcpServers`

这样一份 runtime 既能覆盖工作区本地静态资源，也能覆盖真正走 MCP 协议的远端资源/工具调用。

### 4. transport 先只做 `stdio`
规格只要求 stdio transport，因此当前实现明确只接通 `stdio`。非 stdio profile 在本期会被忽略，不提前引入远端网关、认证或长连接管理复杂度。

### 5. 使用一次性 child process 完成单次 MCP 请求
当前 transport 实现没有做连接池或长连接复用。每次 `resources/list/read`、`tools/list/call` 都会：

- 拉起一个 stdio child process
- 发送 `initialize`
- 发送 `notifications/initialized`
- 发送目标方法请求
- 读取结果并退出

这样虽然不追求吞吐，但实现边界清晰，足以满足 ISSUE-021 的功能和测试范围。

### 6. 使用 Content-Length framing，而不是临时换行协议
MCP 是 stdio 协议而不是“任意 JSON 一行一条”，因此当前实现采用 `Content-Length` framing。测试里的 fake MCP server 也按同样 framing 读写，避免把临时测试协议误当成真正 transport。

### 7. 失败通过 `MCPTransportError` 统一暴露
为满足“失败信息可追踪”，transport 失败不会丢失上下文，而是统一抛出 `MCPTransportError`，其中保留：

- `server_name`
- `method`
- `detail`
- `stderr`
- `exit_code`

这样无效命令、初始化失败、方法错误和超时都能被上层稳定识别。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/extensions/test_mcp_runtime.py` | local resource（1 个） | manifest 本地资源可发现、可读取 |
| `test/extensions/test_mcp_runtime.py` | stdio resource/tool（1 个） | stdio server 可列资源、读资源、列工具、调用工具 |
| `test/extensions/test_mcp_runtime.py` | invalid server（1 个） | 无效 server 会抛出可追踪的 `MCPTransportError` |

## 回归结果

定向验证：

- `python -m unittest discover -s test/extensions -p "test_mcp_runtime.py" -v` → 3/3 OK