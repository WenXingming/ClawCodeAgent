# REFAC Step 05 Implementation Memory

## Step Goal

根据 [docs/architecture/GLOBAL_REFACTOR_PLAN.md](/docs/architecture/GLOBAL_REFACTOR_PLAN.md) 的 Step 5，本步目标是把插件、策略、搜索与 worktree 统一收口到新的 `workspace` 领域中，引入 `WorkspaceGateway` 作为唯一工作区门面，并移除 `LocalAgent` 对旧 `extensions` runtime 细节的直接认知。

## Completed Work

1. 新增 [src/workspace/__init__.py](src/workspace/__init__.py)，建立 `workspace` 领域的公共导出面。
2. 新增 [src/workspace/workspace_gateway.py](src/workspace/workspace_gateway.py)，引入 `WorkspaceGateway` 统一收口工作区能力。
3. 将原工作区 runtime 迁移并重命名为新的领域实现：
   - [src/workspace/plugin_catalog.py](src/workspace/plugin_catalog.py)
   - [src/workspace/policy_catalog.py](src/workspace/policy_catalog.py)
   - [src/workspace/search_service.py](src/workspace/search_service.py)
   - [src/workspace/worktree_service.py](src/workspace/worktree_service.py)
4. 更新 [src/orchestration/local_agent.py](src/orchestration/local_agent.py)，使 agent 只通过 `WorkspaceGateway` 获取：
   - 工具注册表增强
   - budget override
   - plugin summary
   - before/after hooks
   - block 决策
   - search 能力
   - safe env
5. 更新 [src/interaction/command_line_interaction.py](src/interaction/command_line_interaction.py)，把环境摘要与 load error 统计切换到 `workspace_gateway`。
6. 同步迁移并修正以下测试切面：
   - [test/extensions/test_plugin_runtime.py](test/extensions/test_plugin_runtime.py)
   - [test/extensions/test_hook_policy_runtime.py](test/extensions/test_hook_policy_runtime.py)
   - [test/extensions/test_search_runtime.py](test/extensions/test_search_runtime.py)
   - [test/extensions/test_worktree_runtime.py](test/extensions/test_worktree_runtime.py)
   - [test/orchestration/test_local_agent.py](test/orchestration/test_local_agent.py)
   - [test/orchestration/test_query_engine.py](test/orchestration/test_query_engine.py)
   - [test/test_main.py](test/test_main.py)
   - [test/test_main_chat.py](test/test_main_chat.py)
7. 清空生产代码中的旧 `extensions.*` import；`src/extensions/` 仅剩缓存目录，不再承载任何源码模块。

## Production Refactor Outcome

### 1. `LocalAgent` 不再直接掌握工作区 runtime

[src/orchestration/local_agent.py](src/orchestration/local_agent.py) 已删除对 `plugin_runtime`、`hook_policy_runtime`、`search_runtime`、`worktree_runtime` 的直接导入与字段持有。agent 启动时现在只创建一个 `WorkspaceGateway`，并通过它完成工作区能力装配。

### 2. 工作区能力被统一到单一门面

`WorkspaceGateway` 现在负责：

1. 构建和增强工具注册表。
2. 应用策略预算覆盖。
3. 提供 plugin summary 给 prompt。
4. 暴露 tool pipeline 所需的 hooks 与 block 决策。
5. 暴露搜索 provider 与查询执行入口。
6. 暴露合并后的 `safe_env`。

### 3. 旧 `extensions` 目录不再是运行时边界

插件、策略、搜索和 worktree 的实现已全部迁移到 `workspace/`。MCP 继续保留在 `tools/mcp/`，不再与工作区能力混放。

## Import Surface Changes

本步同步完成了以下 import 面收敛：

1. 生产代码从 `extensions.*` 切换到 `workspace.*` 或 `from workspace import ...`。
2. `LocalAgent` 新增 `workspace_gateway` 字段，删除旧 `plugin_runtime`、`hook_policy_runtime`、`search_runtime` 字段。
3. `command_line_interaction.py` 的环境摘要不再读取旧 runtime attr，而是读取 `workspace_gateway` 的聚合计数。
4. 对应测试全部切换到新的类名与模块路径：
   - `PluginCatalog`
   - `PolicyCatalog`
   - `SearchService`
   - `WorktreeService`

## Deleted Old Design

本步明确删除的旧设计：

1. `LocalAgent` 对 plugin/policy/search/worktree runtime 的并列持有。
2. agent 层对工作区 hooks/block/search 逻辑的分散访问。
3. `extensions/` 作为工作区运行时主边界的设计。

## Documentation Updates

本步同步更新了以下活文档，使其与当前实现一致：

1. [README.md](README.md)
2. [docs/architecture/Architecture.md](/docs/architecture/Architecture.md)

## Verification

已通过的 Step 5 定向回归命令：

1. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -p "test_plugin_runtime.py" -v`
2. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -p "test_hook_policy_runtime.py" -v`
3. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -p "test_search_runtime.py" -v`
4. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -p "test_worktree_runtime.py" -v`
5. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_local_agent.py" -v`
6. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_query_engine.py" -v`
7. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_main*.py" -v`

本轮验收结果：

1. `test_plugin_runtime.py` 共 3 项通过。
2. `test_hook_policy_runtime.py` 共 3 项通过。
3. `test_search_runtime.py` 共 8 项通过。
4. `test_worktree_runtime.py` 共 4 项通过。
5. `test_local_agent.py` 共 39 项通过。
6. `test_query_engine.py` 共 4 项通过。
7. `test_main*.py` 共 27 项通过。

合计：88 项测试通过。

## Boundary After Step 5

Step 5 到此停止。

当前已完成的是“工作区领域收口”；下一步尚未开始的是 Step 6，也就是继续处理 `extensions/` 彻底删除后的外围收尾与更高层边界整理。
