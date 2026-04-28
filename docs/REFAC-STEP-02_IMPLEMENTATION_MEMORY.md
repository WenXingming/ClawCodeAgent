# REFAC Step 02 Implementation Memory

## Step Goal

根据 [docs/GLOBAL_REFACTOR_PLAN.md](docs/GLOBAL_REFACTOR_PLAN.md) 的 Step 2，重建 session、CLI 与 resume 的静态契约装配路径，让新会话与恢复会话共用同一条启动规格构建链。

## Completed Work

1. 在 [src/interaction/command_line_interaction.py](src/interaction/command_line_interaction.py) 中新增 `AgentLaunchSpec`，把以下启动输入收口为单个不可变规格：
   - `ModelConfig`
   - `WorkspaceScope`
   - `ExecutionPolicy`
   - `ContextPolicy`
   - `ToolPermissionPolicy`
   - `BudgetConfig`
   - `SessionPaths`
2. 新会话路径不再直接手工拼 `LocalAgent`；改为先生成 `AgentLaunchSpec`，再统一实例化 `OpenAIClient`、`AgentSessionStore` 与 `LocalAgent`。
3. resume 路径不再单独维护一套 client/store/agent 装配逻辑；改为：
   - 先加载 `AgentSessionSnapshot`
   - 再把 CLI 覆盖项合并到快照基线
   - 最后走同一条 `AgentLaunchSpec -> LocalAgent` 装配链
4. 保持 `AgentSessionStore` 为薄持久化边界，不把 CLI 覆盖策略塞回 store 层。

## Structural Outcome

Step 2 后，CLI 的职责被重新切开：

1. 参数解析仍留在 `CLI`。
2. 静态契约合成收口到 `AgentLaunchSpec`。
3. `LocalAgent` 实例化改为统一入口，不再让新建/恢复两条路径重复手写七段依赖注入。
4. session 恢复链只负责“加载快照 + 提供基线”，不再承担 agent 装配细节。

这一步没有回退到新的全局 runtime config 对象；装配规格仅存在于 CLI 边界，服务启动链而不是污染核心契约层。

## Test Updates

1. [test/test_main.py](test/test_main.py) 新增断言：
   - 新会话 `--session-directory` 会同时影响 `session_paths` 与 `AgentSessionStore.directory`
   - resume 场景下 `--session-directory` 会同时影响加载目录与新 agent 的落盘目录
2. [test/session/test_session_store.py](test/session/test_session_store.py) 强化 round-trip 断言，确保 `ExecutionPolicy`、`ContextPolicy` 与 `SessionPaths` 均能稳定落盘并恢复。

## Verification

1. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_main*.py" -v`
2. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/session -p "test_session_*.py" -v`

结果：

1. `test_main*.py` 共 27 项通过。
2. `test_session_*.py` 将作为 Step 2 的最终定向回归执行。

## Boundary After Step 2

Step 2 只处理“静态装配路径”。

尚未进入的下一阶段工作包括：

1. 引入 `AgentRunState`，收口动态执行状态。
2. 继续拆分 `LocalAgent` 的主循环与运行时协作对象。
3. 把工具层、workspace 层与 context 层继续剥离成独立子系统。