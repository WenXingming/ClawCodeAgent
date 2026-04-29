# ISSUE-021 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/tools/mcp_runtime.py` | 新建 | 作为 MCP 运行时门面，协调 manifest、transport 与 renderer |
| `src/tools/mcp_manifest_loader.py` | 新建 | 负责 `.claw/mcp*.json` 发现、解析与 server/resource 归一化 |
| `src/tools/mcp_transport.py` | 新建 | 负责 stdio 与 HTTP/SSE transport 请求和协议编解码 |
| `src/tools/mcp_renderer.py` | 新建 | 负责 MCP 资源与工具结果文本渲染 |
| `src/tools/mcp_models.py` | 新建 | 统一承载 MCP 数据模型与 `MCPTransportError` |
| `src/tools/mcp_tool_adapter.py` | 新建 | 将远端 MCP tools 展开成 Agent 顶层 tool schema |
| `test/extensions/test_mcp_runtime.py` | 新建 | 覆盖资源读取、工具调用、无效 server 错误追踪 |
| `README.md` | 修改 | 补充 MCP Runtime 的 manifest 路径、stdio 能力与示例 |
| `docs/architecture/Architecture.md` | 修改 | 把 mcp runtime 写回架构视图与运行时说明 |

## 关键设计决策

### 1. MCP 运行时迁入 tools 层，并直接接入 agent 工具链
当前实现不再把 MCP 放在 `extensions/` 下作为旁路运行时，而是拆成 `src/tools/mcp_*` 多模块结构，并由 `LocalAgent` 在启动时直接注册资源工具和展开后的远端 tool schema。这样模型能直接看到 `tavily_search`、`tavily_extract` 这类具体函数，而不是再走字符串桥接。

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

### 4. transport 统一落在 `mcp_transport.py`，同时覆盖 `stdio` 与 HTTP/SSE
当前 transport 层支持：

- `stdio`
- `streamable-http`
- `sse`

但仍然刻意不做长连接复用、远端网关或会话池管理，保持一次请求一次初始化的可追踪边界。

### 5. 使用一次性请求完成单次 MCP 交互
当前 transport 实现没有做连接池或长连接复用。对于 `stdio`，每次 `resources/list/read`、`tools/list/call` 都会：

- 拉起一个 stdio child process
- 发送 `initialize`
- 发送 `notifications/initialized`
- 发送目标方法请求
- 读取结果并退出

这样虽然不追求吞吐，但实现边界清晰，足以满足 ISSUE-021 的功能和测试范围。

### 6. stdio 默认使用 Content-Length framing，并保留 JSONL 兼容回退
MCP 的标准 stdio 协议不是“任意 JSON 一行一条”，因此当前实现默认采用 `Content-Length` framing。为了兼容部分实现，还保留 JSONL 回退路径；测试里的 fake MCP server 仍按 framing 读写，避免把临时测试协议误当成真正 transport。

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
