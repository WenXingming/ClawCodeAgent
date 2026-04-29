# REFAC Step 04 Implementation Memory

## Step Goal

根据 [docs/architecture/GLOBAL_REFACTOR_PLAN.md](/docs/architecture/GLOBAL_REFACTOR_PLAN.md) 的 Step 4，本步目标是把工具执行重建为独立子系统，建立统一门面，清理 `LocalAgent` 对工具上下文、工具 schema 构建、bash 流式输出和 MCP 文件布局的直接认知。

## Completed Work

1. 新增 [src/tools/tool_gateway.py](src/tools/tool_gateway.py)，引入 `ToolGateway` 作为工具层唯一门面。
2. 新增 [src/tools/registry.py](src/tools/registry.py)，收口 `LocalTool`、工具注册表构建与 OpenAI tool schema 投影。
3. 新增 [src/tools/executor.py](src/tools/executor.py)，收口：
   - `ToolExecutionContext`
   - `ToolExecutionError`
   - `ToolPermissionError`
   - `ToolStreamUpdate`
   - `ToolExecutor`
4. 新增 [src/tools/local/filesystem_tools.py](src/tools/local/filesystem_tools.py)，把 `list_dir/read_file/write_file/edit_file` 及其路径与文本辅助逻辑从旧混装文件拆出。
5. 新增 [src/tools/local/shell_tools.py](src/tools/local/shell_tools.py)，把 `bash` 工具、shell 权限检查、流式 stdout/stderr 处理、进程超时与输出渲染全部下沉到工具层。
6. 新增 [src/tools/__init__.py](src/tools/__init__.py)，为工具子系统建立明确导出面。
7. 新增 [src/tools/mcp/__init__.py](src/tools/mcp/__init__.py)，为 MCP 子包建立明确导出面。
8. 把旧 MCP 文件迁移为子包结构：
   - [src/tools/mcp/runtime.py](src/tools/mcp/runtime.py)
   - [src/tools/mcp/models.py](src/tools/mcp/models.py)
   - [src/tools/mcp/manifest_loader.py](src/tools/mcp/manifest_loader.py)
   - [src/tools/mcp/renderer.py](src/tools/mcp/renderer.py)
   - [src/tools/mcp/transport.py](src/tools/mcp/transport.py)
9. 删除 [src/tools/local_tools.py](src/tools/local_tools.py) 这个旧的混装入口文件。

## Production Refactor Outcome

### 1. `LocalAgent` 不再掌握工具执行细节

[src/orchestration/local_agent.py](src/orchestration/local_agent.py) 现在只通过 `ToolGateway` 做三件事：

1. 构建基础工具注册表。
2. 构建当前轮次工具执行上下文。
3. 执行工具调用并接收流式更新回调。

`LocalAgent` 已不再直接分支 `bash` 的执行方式，也不再自己构造 OpenAI tool schema 列表。

### 2. 工具 schema 与流式执行细节被彻底下沉

1. 工具 schema 投影从 agent/slash 内联实现，迁移到 [src/tools/registry.py](src/tools/registry.py)。
2. 流式 shell 输出读取、超时处理、子进程管理与最终结果封装，全部迁移到 [src/tools/local/shell_tools.py](src/tools/local/shell_tools.py)。
3. `ToolGateway.execute_call()` 成为统一入口，agent 只处理“是否上报 chunk”，不处理工具内部执行分支。

### 3. MCP 的目录边界被重建

MCP 相关实现不再散落在 `src/tools/` 根目录，而是集中在 `src/tools/mcp/` 包中。上层现在通过 `tools.mcp` 的公共导出面使用 MCP 运行时与模型对象。

## Import Surface Changes

本步同步完成了相关 import 面收敛：

1. `AgentRunState`、`PluginRuntime`、`HookPolicyRuntime`、`SlashCommandDispatcher` 都改为引用新的 `tools.registry` / `tools.executor` / `tools.mcp`。
2. `LocalAgent` 的工具依赖从 `LocalToolService` 切换为 `ToolGateway`。
3. 对应单元测试也全部切换到新门面与新包路径。

## Deleted Old Design

本步明确删除的旧设计：

1. 旧混装工具入口 [src/tools/local_tools.py](src/tools/local_tools.py)。
2. `LocalAgent` 中对 `bash` 工具的专门执行分支。
3. `LocalAgent` 内联构造 OpenAI tool schema 的实现。
4. `src/tools/` 根目录下旧的 MCP 平铺文件布局。

## Test Updates

本步修改并通过了以下测试切面：

1. [test/tools/test_local_tools.py](test/tools/test_local_tools.py)
2. [test/tools/test_local_tools_shell.py](test/tools/test_local_tools_shell.py)
3. [test/interaction/test_slash_commands.py](test/interaction/test_slash_commands.py)
4. [test/extensions/test_plugin_runtime.py](test/extensions/test_plugin_runtime.py)
5. [test/extensions/test_hook_policy_runtime.py](test/extensions/test_hook_policy_runtime.py)
6. [test/extensions/test_mcp_runtime.py](test/extensions/test_mcp_runtime.py)
7. [test/orchestration/test_local_agent.py](test/orchestration/test_local_agent.py)
8. [test/orchestration/test_query_engine.py](test/orchestration/test_query_engine.py)

## Verification

已通过的 Step 4 定向回归命令：

1. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/tools -v`
2. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/interaction -p "test_slash_commands.py" -v`
3. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -p "test_plugin_runtime.py" -v`
4. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -p "test_hook_policy_runtime.py" -v`
5. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -p "test_mcp_runtime.py" -v`
6. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_local_agent.py" -v`
7. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_query_engine.py" -v`

本轮验收结果：

1. `test/tools` 共 26 项通过。
2. `test_slash_commands.py` 共 14 项通过。
3. `test_plugin_runtime.py` 共 3 项通过。
4. `test_hook_policy_runtime.py` 共 3 项通过。
5. `test_mcp_runtime.py` 共 8 项通过。
6. `test_local_agent.py` 共 39 项通过。
7. `test_query_engine.py` 共 4 项通过。

合计：97 项测试通过。

## Boundary After Step 4

Step 4 到此停止。

当前已完成的是“工具层重建”；下一步尚未开始的是 Step 5，也就是把插件、策略、搜索与 worktree 统一收口到新的 workspace 领域门面中。
