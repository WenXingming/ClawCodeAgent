# 快速开始（设置 API Key 直接实验）

## 1. 前置条件

- 已在根目录打开工程：D:/WorkSpace/ClawCodeAgent
- Python 环境可用（当前建议使用 C:/ProgramData/anaconda3/python.exe）
- 你有可用的 OpenAI-compatible 后端地址、模型名和 API Key

## 2. 设置环境变量（PowerShell）

```powershell
$env:OPENAI_MODEL = "your-model-name"
$env:OPENAI_BASE_URL = "http://127.0.0.1:8000/v1"
$env:OPENAI_API_KEY = "your-api-key"
```

`src/` 现在是源码根目录，不再作为 `src` 包名参与导入。代码里应使用 `from core_contracts...`、`from orchestration...`、`from planning...`、`from extensions...`、`from budget...` 这类顶层绝对导入。

## 3. 运行一次最小实验

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py agent "请读取当前目录结构并简要总结"
```

## 4. 常用参数示例

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py agent \
  --cwd . \
  --max-turns 8 \
  --allow-file-write \
  "请在当前目录创建一个 demo.txt 并写入 hello"

# powershell 里反斜杠并不作为续行符，因此需要把参数写在一行，或使用反引号 ` 作为续行符：
C:/ProgramData/anaconda3/python.exe ./src/main.py agent `
  --cwd . `
  --max-turns 8 `
  --allow-file-write `
  "请在当前目录创建一个 demo.txt 并写入 hello"

C:/ProgramData/anaconda3/python.exe ./src/main.py agent --cwd . --max-turns 8 --allow-file-write "请在当前目录创建一个 demo.txt 并写入 hello"
```

## 5. 交互式聊天（agent-chat）

```powershell
# 新会话进入交互模式
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-chat

# 带一条初始问题进入交互模式
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-chat "先帮我看当前目录结构"
```

进入循环后可继续输入新问题；输入 `.exit` 或 `.quit` 退出。

## 6. 续跑已保存会话（agent-resume）

每次运行后会在 `.port_sessions/agent/` 目录生成一个 `<session_id>.json` 文件。  
使用 `agent-resume` 可从上次结束的上下文继续执行：

```powershell
# 查找 session_id（从上次运行的输出或会话目录获得）
Get-ChildItem .port_sessions\agent\

# Resume（默认继承上次保存的 model/runtime 配置，也可显式覆盖部分参数）
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-resume <session_id> "继续上次任务"
```

**常见错误**
- `Session not found`：session_id 不存在或文件已删除，请重新 run。
- `Corrupted session file`：session 文件损坏，无法恢复，请重新 run。

## 7. 本地 Slash 控制面命令

以下命令通过 `agent`、`agent-resume` 或 `agent-chat` 的 prompt 入口传入，但会在本地先行分流，不触发模型调用：

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py agent "/help"
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-resume <session_id> "/status"
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-chat --session-id <session_id>
# 在 chat 里输入 /help、/status、/clear
```

当前支持的高频本地命令：

- `/help`：列出支持的本地 slash 命令。
- `/context`：查看当前会话上下文概览、token 投影和压缩阈值。
- `/status`：查看当前 session_id、模型、工作目录、累计 turns/tool_calls。
- `/permissions`：查看当前工具权限开关。
- `/tools`：列出当前注册的本地工具。
- `/clear`：保留旧 session 文件，生成新的 cleared session 快照。

这些命令只会写入 `slash_command` event，不会写入模型 transcript。

## 8. 工作区插件（ISSUE-014）

当前版本支持从工作区自动发现插件 manifest，并注册两类工具，同时也允许插件在工具执行链里注入 hook / block：

- alias tool：把现有工具包装成新的名字，并可注入固定参数。
- virtual tool：注册一个不触发底层文件/shell 的虚拟工具，直接返回固定内容。
- hook / block：在工具执行前后注入 system message，或按工具名/前缀阻断调用。

发现路径：

- `.claw/plugins.json`
- `.claw/plugins/*.json`

示例：

```json
{
  "name": "demo-plugin",
  "summary": "Expose README alias and workspace banner.",
  "deny_tools": ["edit_file"],
  "before_hooks": [
    {
      "kind": "message",
      "content": "plugin before"
    }
  ],
  "after_hooks": [
    {
      "kind": "message",
      "content": "plugin after"
    }
  ],
  "aliases": [
    {
      "name": "read_readme",
      "target": "read_file",
      "description": "Read README.md through plugin alias.",
      "arguments": {
        "path": "README.md"
      }
    }
  ],
  "virtual_tools": [
    {
      "name": "workspace_banner",
      "description": "Return a fixed plugin banner.",
      "content": "Workspace banner from plugin runtime."
    }
  ]
}
```

将上面的 JSON 保存到 `.claw/plugins/demo.json` 后，新建的 `LocalCodingAgent` 会在启动时自动装载插件工具；执行 `/tools` 时也会额外显示已发现插件摘要。若配置了 `before_hooks` / `after_hooks` / `deny_*`，这些规则会在 tool pipeline 中生效，并写入 transcript/event metadata。

冲突策略：核心工具和先注册成功的工具优先；若插件工具名称冲突，冲突项会被跳过，并出现在 `/tools` 的插件摘要里。

## 9. 工作区 Policy（ISSUE-015）

当前版本支持从工作区自动发现 hook/policy manifest，并在 agent 初始化时应用四类治理能力：

- deny 规则：可按精确工具名或名前缀移除工具。
- safe env：把白名单环境变量注入到工具上下文；当前主要影响 `bash` 工具的子进程环境。
- budget override：覆盖运行时 `BudgetConfig`，在真正进入主循环前生效。
- hook 注入：在工具执行前后追加 system message，并把来源写入 tool result metadata 与 runtime events。

发现路径：

- `.claw/policies.json`
- `.claw/policies/*.json`

示例：

```json
{
  "name": "workspace-policy",
  "trusted": true,
  "deny_tools": ["edit_file"],
  "deny_prefixes": ["workspace_"],
  "safe_env": {
    "POLICY_MODE": "strict"
  },
  "budget_overrides": {
    "max_model_calls": 4,
    "max_tool_calls": 8
  },
  "before_hooks": [
    {
      "kind": "message",
      "content": "before hook placeholder"
    }
  ],
  "after_hooks": [
    {
      "kind": "message",
      "content": "after hook placeholder"
    }
  ]
}
```

`trusted=false` 的 manifest 会被跳过，不会进入有效 policy 合并结果。多个 trusted manifest 会按文件排序依次合并：

- `deny_tools` / `deny_prefixes` 追加去重。
- `safe_env` 后者覆盖前者的同名 key。
- `budget_overrides` 只覆盖显式给出的非空字段。

说明：当前版本会在 tool pipeline 中执行 `before_hooks` / `after_hooks`，并按“policy 优先于 plugin”的顺序判断阻断；阻断结果不会真的执行底层工具，但仍会以同一个 `tool_call_id` 回填一条 tool result，便于后续模型轮次继续消费。

## 10. 工作区 Task Runtime（ISSUE-017）

当前版本支持从工作区加载本地任务状态机，用于维护任务的创建、更新、开始、完成、阻塞、取消，以及基于依赖关系选择下一批可执行任务。

持久化路径：

- `.claw/tasks.json`

当前能力：

- `create_task`：创建任务；若依赖尚未完成，任务会自动进入 `blocked`。
- `update_task`：更新标题、描述和依赖列表，并立即重新计算依赖阻塞状态。
- `start_task` / `complete_task`：执行标准状态流转 `pending -> in_progress -> completed`。
- `block_task` / `cancel_task`：显式把任务标记为 `blocked` 或 `cancelled`。
- `list_tasks` / `next_tasks`：列出所有任务，或返回当前可执行的 `pending` 任务。

状态集合：

- `pending`
- `in_progress`
- `completed`
- `blocked`
- `cancelled`

示例：

```python
from pathlib import Path

from planning.task_runtime import TaskRuntime

runtime = TaskRuntime.from_workspace(Path('.'))
runtime.create_task('task-001', '实现 Task Runtime')
runtime.create_task('task-002', '为 Plan Runtime 预留同步点', dependencies=('task-001',))

runtime.start_task('task-001')
runtime.complete_task('task-001')

print([task.task_id for task in runtime.next_tasks()])
# ['task-002']
```

说明：每次变更操作都会自动写回 `.claw/tasks.json`。当前版本已由 Plan Runtime 接入 plan-task 同步；跨仓库任务同步仍不在本期范围内。

## 11. 工作区 Plan Runtime（ISSUE-018）

当前版本支持从工作区加载本地计划运行时，用于维护 `PlanStep` 列表、渲染计划视图，并把计划步骤稳定同步到 Task Runtime。

持久化路径：

- `.claw/plan.json`

当前能力：

- `update_plan`：更新步骤列表，并可选择立即同步到任务列表。
- `clear_plan`：清空计划，并可选择同时清理任务列表。
- `sync_tasks`：把计划步骤映射为任务，更新依赖关系，并把任务状态回写到计划步骤状态。
- `render_plan`：输出稳定的文本计划视图，便于 CLI 或后续控制面消费。

状态集合：

- `pending`
- `in_progress`
- `completed`
- `blocked`
- `cancelled`

示例：

```python
from pathlib import Path

from planning.plan_runtime import PlanRuntime, PlanStep

runtime = PlanRuntime.from_workspace(Path('.'))
runtime.update_plan(
  (
    PlanStep(step_id='step-001', title='收集上下文'),
    PlanStep(step_id='step-002', title='实现代码', dependencies=('step-001',)),
  ),
  sync_tasks=True,
)

print(runtime.render_plan())
```

说明：`sync_tasks()` 会把 `PlanStep.step_id` 映射为 `TaskRecord.task_id`，并保持依赖关系一致；若对应任务已存在，其执行状态会在同步后回写到计划步骤中。图形化计划编辑器和跨仓库同步仍不在本期范围内。

## 12. 工作区 Workflow Runtime（ISSUE-019）

当前版本支持从工作区发现 workflow manifest，执行一组基于 Task Runtime 的操作序列，并把每次运行的结果持久化为历史记录。

发现与持久化路径：

- `.claw/workflows.json`
- `.claw/workflows/*.json`
- `.claw/workflow_runs.json`

当前能力：

- `list_workflows` / `get_workflow`：查询已发现的工作流定义。
- `run_workflow`：按 manifest 中的步骤顺序执行 `create/update/start/complete/block/cancel` 等 Task Runtime 操作。
- `history`：读取已持久化的运行记录，便于回放和诊断。

步骤动作集合：

- `create`
- `update`
- `start`
- `complete`
- `block`
- `cancel`

示例：

```json
{
  "workflow_id": "demo-workflow",
  "title": "Demo Workflow",
  "steps": [
    {
      "action": "create",
      "task_id": "task-001",
      "title": "准备任务"
    },
    {
      "action": "start",
      "task_id": "task-001"
    },
    {
      "action": "complete",
      "task_id": "task-001"
    }
  ]
}
```

说明：工作流运行记录会包含每一步的前后状态、是否成功和错误信息，因此失败流程也能在历史文件中被诊断。当前版本只做本地顺序执行，不包含分布式调度。

## 13. 工作区 Search Runtime（ISSUE-020）

当前版本支持从工作区和环境变量发现搜索 provider，切换当前 active provider，并通过真实 HTTP 后端执行结构化检索。

发现与持久化路径：

- `.claw/search.json`
- `.claw/search/*.json`
- `.claw/search_state.json`

当前环境变量发现：

- `SEARXNG_BASE_URL`
- `CLAW_SEARCH_PROVIDER`（可选，用于指定默认 active provider）

provider 可选鉴权：

- `api_key_env`（可选，provider profile 指定后会从对应环境变量读取 API Key，并附带 `Authorization: Bearer` 与 `X-API-Key` 请求头）

当前能力：

- `list_providers` / `get_provider`：列出和读取已发现 provider profile。
- `activate_provider`：切换 active provider，并把结果持久化到 `.claw/search_state.json`。
- `search`：执行结构化检索，返回 provider、query、attempts 和结果列表。

天气查询兜底：

- 当 `duckduckgo` 查询结果为空且 query 命中天气关键词（`weather` / `天气` / `forecast` 等）时，runtime 会自动调用 `wttr.in` 返回一条天气概览结果，降低“connection issue”空返回概率。

当前已接通后端：

- `searxng`
- `duckduckgo`

示例：

```json
{
  "provider_id": "workspace-search",
  "provider": "searxng",
  "title": "Workspace Search",
  "base_url": "http://127.0.0.1:8080",
  "default_max_results": 5,
  "description": "Local SearxNG provider for workspace search."
}
```

DuckDuckGo provider 示例（在公网 SearxNG 返回 403/429 时可作为降级）：

```json
{
  "provider_id": "duckduckgo-web",
  "provider": "duckduckgo",
  "title": "DuckDuckGo Web Search",
  "base_url": "https://api.duckduckgo.com",
  "default_max_results": 5,
  "description": "DuckDuckGo instant answer API based search provider."
}
```

代码示例：

```python
from pathlib import Path

from extensions.search_runtime import SearchRuntime

runtime = SearchRuntime.from_workspace(Path('.'))
runtime.activate_provider('workspace-search')
response = runtime.search('ClawCodeAgent runtime design', max_retries=1)

print(response.provider.provider_id)
print(response.attempts)
print(response.results[0].title)
```

说明：当前版本不做多 provider 融合排序；查询失败时会按 `max_retries` 重试，重试耗尽后抛出带 `provider_id`、`attempts` 和错误文本的 `SearchQueryError`，便于上层做可控处理。

## 14. 工作区 MCP Runtime（ISSUE-021）

当前版本支持从工作区发现 MCP manifest，读取本地 manifest 资源，并通过 stdio transport 调用 MCP server 的 `resources/list/read` 与 `tools/list/call`。

发现路径：

- `.claw/mcp.json`
- `.claw/mcp/*.json`

当前能力：

- `list_resources` / `read_resource`：列出和读取 manifest 资源，或通过 stdio server 读取远端 MCP 资源。
- `list_tools` / `call_tool`：列出和调用 stdio MCP server 暴露的工具。
- `render_summary` / `render_resource_index` / `render_tool_index`：输出可读的 MCP 资源和工具概览。

当前已接通 transport：

- `stdio`

示例：

```json
{
  "servers": [
    {
      "name": "workspace",
      "resources": [
        {
          "uri": "mcp://workspace/notes",
          "name": "Notes",
          "path": "notes.txt"
        }
      ]
    }
  ],
  "mcpServers": {
    "remote": {
      "command": "python",
      "args": ["-u", "fake_mcp_server.py"]
    }
  }
}
```

代码示例：

```python
from pathlib import Path

from extensions.mcp_runtime import MCPRuntime

runtime = MCPRuntime.from_workspace(Path('.'))
print(runtime.list_resources())
print(runtime.read_resource('mcp://workspace/notes'))

tool_result = runtime.call_tool('echo', arguments={'text': 'hello'}, server_name='remote')
print(tool_result.content)
```

说明：当前版本不做远端 MCP 网关和长连接复用；每次 transport 请求都会拉起一次 stdio child process，完成 `initialize` 和目标方法调用。失败会抛出带 `server_name`、`method`、`stderr` 和 `exit_code` 的 `MCPTransportError`，便于上层追踪。

## 15. 预算控制（BudgetConfig）

通过 `BudgetConfig` 可以为每次运行设置多维度的安全上限：

| 字段 | 说明 | 触发 stop_reason |
|------|------|-----------------|
| `max_input_tokens` | 输入 token 硬上限（char/4 估算） | `token_limit` |
| `max_total_cost_usd` | 会话总成本上限（USD） | `cost_limit` |
| `max_tool_calls` | 工具调用次数上限 | `tool_call_limit` |
| `max_model_calls` | 模型调用次数上限 | `model_call_limit` |
| `max_session_turns` | 会话累计轮数上限（含 resume 历史） | `session_turns_limit` |

代码示例：

```python
from core_contracts.config import AgentRuntimeConfig, BudgetConfig
from orchestration.agent_runtime import LocalCodingAgent

config = AgentRuntimeConfig(
    cwd='.',
    budget_config=BudgetConfig(
        max_input_tokens=32_000,   # 限制 prompt 不超过约 32K token
        max_total_cost_usd=0.10,   # 整个会话最多花 0.1 USD
        max_tool_calls=20,         # 最多调用工具 20 次
        max_model_calls=10,        # 最多调用模型 10 次
        max_session_turns=50,      # 含 resume 累计轮数不超过 50
    ),
)
agent = LocalCodingAgent(client, config)
result = agent.run('...')
print(result.stop_reason)  # 预算超限时返回对应的 *_limit 字符串
```

**软超限（is_soft_over）**：当 prompt 接近上限但尚未触发硬停止时，`token_budget` event 中的 `is_soft_over=True`，ISSUE-010/011 的 snip/compact 将据此压缩上下文。

## 16. CLI 迁移说明

- 旧用法 `python src/main.py "prompt"` 已不再支持。
- 旧用法 `python src/main.py --session-id <id> "prompt"` 已不再支持。
- 新命令面固定为：`agent`、`agent-chat`、`agent-resume`。

## 17. 说明

- `--model`、`--base-url`、`--api-key` 都支持命令行覆盖。
- 若不传命令行参数，程序会回退读取环境变量：
  - OPENAI_MODEL
  - OPENAI_BASE_URL
  - OPENAI_API_KEY
- 默认是安全权限：不允许 shell，不允许危险 shell 命令。
- 从仓库根执行 Python 命令时，`sitecustomize.py` 会自动把 `src/` 注入 `sys.path`，因此测试与脚本都按源码根模式运行。
- 递归测试发现的标准命令是 `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v`；它通过 `test/test_all.py` 在无 `__init__.py` 的测试树上继续递归装载所有测试。
