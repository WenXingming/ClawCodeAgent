# ISSUE-014 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/extensions/plugin_runtime.py` | 新建 | 实现 plugin manifest 发现、alias/virtual tool 注册、冲突记录与摘要渲染 |
| `src/orchestration/agent_runtime.py` | 修改 | `LocalCodingAgent` 初始化时自动装载工作区插件并合并到 tool registry |
| `src/interaction/slash_commands_interaction.py` | 修改 | `/tools` 支持渲染插件摘要 |
| `test/extensions/test_plugin_runtime.py` | 新建 | 覆盖 alias 注册、virtual 执行、冲突处理 |
| `test/orchestration/test_agent_runtime.py` | 修改 | 增加工作区插件被主循环真实调用的集成测试 |
| `test/interaction/test_slash_commands.py` | 修改 | 增加 `/tools` 插件摘要渲染测试 |
| `docs/Architecture.md` | 修改 | 写回 plugin runtime 在当前架构中的位置 |
| `README.md` | 修改 | 补充工作区插件 manifest 发现路径、示例与冲突策略 |

## 关键设计决策

### 1. 插件 runtime 独立成 `extensions/plugin_runtime.py`，不把 manifest 逻辑塞进工具层
Issue 014 的控制点是“工具注册表如何构建”，不是单个工具如何执行。把 manifest 发现、校验、冲突判定和摘要渲染收敛到独立 runtime 模块，可以让 `tools/local_tools.py` 继续只关心基础工具定义与执行，不承担工作区扫描职责。

### 2. 采用工作区本地 manifest 约定，而不是新增全局配置项
当前实现默认扫描：

- `.claw/plugins.json`
- `.claw/plugins/*.json`

这样 `LocalCodingAgent(runtime_config.cwd=...)` 就能基于工作区自举插件，不需要先改 CLI/config 契约，也不把 ISSUE-014 扩成“全局插件配置系统”。

### 3. 冲突策略明确为“核心/已注册优先，插件跳过并记录”
当插件工具名与核心工具或已注册插件工具冲突时，当前实现不会覆盖已有工具，而是跳过冲突项并写入 `PluginConflict`。这样行为稳定、可预测，也不会因为工作区落了一个 manifest 就悄悄改变基础工具语义。

### 4. alias tool 复用目标工具 handler，而不是二次走 `execute_tool`
alias handler 直接调用目标 `LocalTool.handler`，继承原工具的权限检查、路径约束和异常语义；同时在成功结果 metadata 中附加 `plugin_name` / `plugin_tool_kind` / `plugin_tool_target`。这样不会把底层错误误包装成“alias 成功”。

### 5. 插件摘要挂到 `/tools`，不新增额外 slash 命令
Issue 的 DoD 只要求“插件摘要可渲染”，当前最自然的观测面就是现有 `/tools`。因此实现为 `SlashCommandContext.plugin_summary`，由 agent 预先注入已发现插件摘要；`/tools` 在列出工具后追加插件区块即可，无需新增 `/plugins` 命令面。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/extensions/test_plugin_runtime.py` | alias + virtual（2 个） | manifest 发现、alias 注册、virtual 执行、冲突跳过、摘要内容 |
| `test/orchestration/test_agent_runtime.py` | `test_run_loads_virtual_tool_from_workspace_plugin_manifest` | `LocalCodingAgent` 初始化时真实装载工作区插件，并在主循环里执行插件工具 |
| `test/interaction/test_slash_commands.py` | `test_dispatch_tools_renders_plugin_summary_when_present` | `/tools` 输出包含插件摘要区块 |

## 回归结果

定向验证：

- `python -m unittest discover -s test/extensions -p "test_plugin_runtime.py" -v` → 2/2 OK
- `python -m unittest discover -s test/orchestration -p "test_agent_runtime.py" -v` → 插件装载集成场景 OK
- `python -m unittest discover -s test/interaction -p "test_slash_commands.py" -v` → `/tools` 插件摘要场景 OK
- `python -m unittest discover -s test -v` → 190/190 OK

