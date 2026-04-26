# ISSUE-012 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/interface/slash_commands_interface.py` | 修改 | 重构为 `SlashCommandDispatcher` 分发器类，保留兼容包装函数，并补齐文件级到函数级注释 |
| `src/orchestration/agent_runtime.py` | 修改 | 在 prompt 写入 session 前接入 slash 预分流与本地结果落盘 |
| `test/interface/test_slash_commands.py` | 新建 | slash 解析与命令分发单元测试 |
| `test/orchestration/test_agent_runtime.py` | 追加 | `/help`、`/status`、`/clear` 的 no-model-call 集成测试 |
| `docs/Architecture.md` | 修改 | 主图补充 `interface/slash_commands_interface.py` 控制面节点 |
| `docs/FINAL_ARCHITECTURE_PLAN.md` | 修改 | ISSUE-012 章节写回实际落地决策 |
| `README.md` | 修改 | 增加本地 slash 控制面命令使用示例 |

## 关键设计决策

### 1. slash 分流必须发生在 `session.append_user()` 之前
如果在 prompt 写入 `messages/transcript` 之后再判断是否为 slash，本地命令就会污染会话历史，后续还需要补偿式回滚。最终实现直接把分流放在 `LocalCodingAgent.run/resume` 入口，只有继续 query 的输入才会进入 `append_user()`。

### 2. 本地命令只写 event，不写 transcript
slash 命令本质上是控制面查询或本地状态变更，不属于模型对话内容。实现里统一写入 `slash_command` event，并返回 `stop_reason='slash_command'`，同时保持 `messages` 与 `transcript` 不变。

### 3. `/clear` 使用 fork 语义，而不是覆盖旧 session
在 resume 场景下，用户需要既保留原有会话以便审计和回看，又获得一个真正清空的可继续会话。最终实现为：旧 session 文件不动，新建一个 cleared `session_id`，保存空 `messages`、空 `transcript`、0 turns、0 tool_calls 的新快照。

### 4. slash 模块保持“轻宿主、重分发”
`src/interface/slash_commands_interface.py` 不直接依赖 `LocalCodingAgent`，而是通过 `SlashCommandContext` 读取会话、配置、模型和工具注册表。这样避免了 runtime 与 slash 模块的循环依赖，也方便单测直接构造上下文。

### 5. 分发逻辑收敛为面向对象分发器，但保留兼容入口
在不改动 runtime 调用方式的前提下，模块内部已重构为 `SlashCommandDispatcher`。状态性依赖（如 `ContextBudgetEvaluator`、命令规格索引）集中在对象内部管理，模块级 `parse_slash_command()` / `dispatch_slash_command()` / `get_slash_command_specs()` / `find_slash_command()` 仅作为默认分发器的兼容包装层。

### 6. `/context` 复用既有 token 预算投影
本期没有新增独立的上下文估算逻辑，而是直接复用 `ContextBudgetEvaluator.evaluate()` 与工具 schema 投影，让控制面看到的 projected tokens 与 runtime 预算检查语义保持一致。

## 测试覆盖（新增 +10）

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/interface/test_slash_commands.py` | 解析测试（2 个） | slash 命令提取、普通文本透传 |
| `test/interface/test_slash_commands.py` | 分发测试（4 个） | 未知命令、`/context`、`/tools`、`/clear` 的本地结果语义 |
| `test/interface/test_slash_commands.py` | 分发器公有 API（1 个） | 直接验证 `SlashCommandDispatcher.parse/find` 的 OO 入口行为 |
| `test/orchestration/test_agent_runtime.py` | `test_run_help_slash_bypasses_model_and_transcript` | `/help` 不触发模型、消息与 transcript 保持空 |
| `test/orchestration/test_agent_runtime.py` | `test_resume_status_slash_bypasses_model_and_preserves_history` | `/status` 在 resume 场景下不触发模型且不污染历史 |
| `test/orchestration/test_agent_runtime.py` | `test_resume_clear_slash_forks_new_session` | `/clear` 保留旧 session 并生成新的 cleared session |

## 回归结果

已运行的定向验证：

- `python -m unittest discover -s test/orchestration -p "test_agent_runtime.py" -v` → 相关 slash 集成场景 OK
- `$env:PYTHONPATH='src'; python -m unittest discover -s test/interface -p "test_slash_commands.py" -v` → 8/8 OK

最终全量回归结果见本次实施结束时的验证记录。