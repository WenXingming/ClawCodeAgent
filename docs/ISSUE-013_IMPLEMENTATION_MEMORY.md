# ISSUE-013 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/control_plane/__init__.py` | 新建 | 建立 control_plane 包边界 |
| `src/control_plane/slash_commands.py` | 新建 | 下沉 slash 命令实现为控制面子模块 |
| `src/control_plane/cli.py` | 新建/扩展 | 子命令 parser、配置覆盖、chat loop 与命令装配 |
| `src/main.py` | 修改 | 收敛为极薄的 CLI 进程入口包装层 |
| `src/orchestration/agent_runtime.py` | 修改 | slash 导入切换到 control_plane 包 |
| `test/test_main.py` | 修改 | 迁移到 `agent` / `agent-resume` 子命令并覆盖配置覆盖逻辑 |
| `test/test_main_chat.py` | 新建 | `agent-chat` 交互循环与 `/clear` 会话切换测试 |
| `test/control_plane/test_slash_commands.py` | 修改 | 测试导入切换到 `control_plane.slash_commands` |
| `docs/Architecture.md` | 修改 | 架构图更新为 `control_plane/cli.py + control_plane/slash_commands.py` |
| `docs/FINAL_ARCHITECTURE_PLAN.md` | 修改 | ISSUE-013 章节写回实际落地决策 |
| `README.md` | 修改 | 命令示例切换到 `agent` / `agent-chat` / `agent-resume` |

## 关键设计决策

### 1. slash 与 CLI 应组织到同一控制面包内，但 `main.py` 不能消失
ISSUE-013 后，CLI 不再只是一个最小脚本，而是带子命令、覆盖逻辑与 chat loop 的命令面。把 CLI 和 slash 一起下沉到 `src/control_plane/` 可以把“控制面复杂度”从源码根移走，同时保留 `src/main.py` 作为稳定进程入口与测试 patch 点。

### 2. CLI 采用强制子命令，不保留旧的顶层 prompt 入口
本期按既定决策做成显式 breaking change：只有 `agent`、`agent-chat`、`agent-resume` 三个命令保留为正式入口。这样 parser、README、测试与未来扩展都更清晰，不需要继续维护两套命令面。

### 3. resume/chat 配置覆盖使用 dataclass `replace()`
`ModelConfig`、`AgentRuntimeConfig`、`BudgetConfig`、`AgentPermissions`、`ModelPricing` 都是 dataclass，CLI 显式参数覆盖时统一使用 `replace()` 生成新配置，避免手写 dict merge 破坏类型与默认值边界。

### 4. 三态参数是 resume/chat 覆盖语义的关键
权限和部分 runtime 开关若继续使用简单 `store_true`，CLI 无法区分“用户未传”与“用户显式关闭”。最终实现使用 `BooleanOptionalAction` 配合 `None` 默认值，让 `agent-resume` 和 `agent-chat --session-id` 能正确继承或覆盖存档设置。

### 5. chat loop 必须追踪 `result.session_id`
ISSUE-012 的 `/clear` 会 fork 新 session；如果 chat loop 仍然固定使用初始 session_id，下一轮就会错误续接旧会话。最终实现每轮都根据 `result.session_id` 和 `result.session_path` 更新当前会话游标。

## 测试覆盖（新增/迁移）

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/test_main.py` | 子命令入口（4 个） | 必需子命令、拒绝旧裸 prompt、`agent` 基本执行、环境变量回退 |
| `test/test_main.py` | `agent-resume`（3 个） | resume 路径、缺失 session 错误、存档配置覆盖 |
| `test/test_main.py` | 权限验证（2 个） | `allow_destructive_shell` 依赖 `allow_shell`，显式权限映射正确 |
| `test/test_main_chat.py` | chat 基础行为（4 个） | 初始 prompt、resume 续聊、`.quit` 退出、EOF 退出 |
| `test/test_main_chat.py` | `/clear` 会话切换（1 个） | chat loop 在 slash fork 后切换到新的 session_id |
| `test/control_plane/test_slash_commands.py` | 模块导入迁移 | slash 测试转到 `control_plane.slash_commands` |

## 回归结果

定向验证：

- `python -m unittest discover -s test -p "test_main*.py" -v` → 15/15 OK
- `python -m unittest discover -s test/control_plane -p "test_slash_commands.py" -v`（control_plane 包化后）→ OK

最终全量回归结果见本次实施结束时的统一验证记录。