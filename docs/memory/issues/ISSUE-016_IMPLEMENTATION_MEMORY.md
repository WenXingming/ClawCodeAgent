# ISSUE-016 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/extensions/plugin_runtime.py` | 修改 | 插件 manifest 新增 hook/block 字段解析，并暴露 tool pipeline helper |
| `src/extensions/hook_policy_runtime.py` | 修改 | policy runtime 新增 execution-time block 决策与 hook helper |
| `src/orchestration/agent_runtime.py` | 修改 | 在 tool loop 中接入 preflight、block、after-hook 与审计事件 |
| `src/session/session_state.py` | 修改 | 新增 runtime system message 追加接口，保证 transcript 与 messages 同步 |
| `test/orchestration/test_agent_runtime.py` | 修改 | 增加插件阻断、策略阻断、双重注入三条主循环集成测试 |
| `test/extensions/test_plugin_runtime.py` | 修改 | 增加 plugin helper 单元测试 |
| `test/extensions/test_hook_policy_runtime.py` | 修改 | 增加 policy helper 单元测试 |
| `README.md` | 修改 | 更新 plugin/policy manifest 的 hook/block 能力说明 |
| `docs/architecture/Architecture.md` | 修改 | 写回 tool pipeline 中 plugin/policy 接入点 |

## 关键设计决策

### 1. 仍然以 `LocalCodingAgent._execute_loop()` 作为 ISSUE-016 的唯一执行接缝
ISSUE-014/015 已经把静态装载动作放在 `__post_init__()`；ISSUE-016 真正需要新增的是“每次工具调用前后”的动态行为。因此实现没有改模型调用骨架，而是在单个 `tool_call` 循环里插入统一的 preflight / block / after 处理层。

### 2. block 判定保留在 execution-time，且 policy 优先于 plugin
ISSUE-015 已经会在初始化阶段过滤 policy deny 对应的 tool registry，用于减少模型可见面；但 ISSUE-016 还要求明确阻断消息和可追踪审计。因此当前实现保留 registry filter，同时在 execution-time 再做一次 block 判定：

- 先查 policy block
- 再查 plugin block
- 未命中时才真正执行底层工具

这样既不破坏 ISSUE-015 的“模型侧看不到被禁工具”，也满足 ISSUE-016 的“被调用时能给出明确阻断结果”。

### 3. hook 注入统一写成 `system` message，并同步进入 transcript
原来的 `AgentSessionState` 只支持 user / assistant / tool 三类追加接口，无法稳定表达 runtime 注入消息。当前新增 `append_runtime_message()`，把 preflight 和 after-hook 都写为 `role=system` 的消息；这样：

- 下一轮模型调用能看到这些提醒
- transcript 顺序完整可审计
- 不需要伪造 assistant/tool 角色去承载 hook 文本

### 4. 阻断不抛异常，而是回填一条合成的 `ToolExecutionResult`
若工具被 plugin/policy 阻断，当前不会执行底层 handler，也不会让主循环走异常路径；而是生成一条 `ok=False` 的结构化 tool result，并沿用原始 `tool_call_id` 回填进消息流。这样下一轮模型仍然能按标准 OpenAI tool-call 语义继续消费结果。

### 5. 审计信息优先落到 tool metadata 和 runtime events
ISSUE-016 需要为后续审计保留足够上下文。当前实现把以下信息写入 `ToolExecutionResult.metadata`：

- `preflight_sources`
- `after_hook_sources`
- `blocked_by`
- `blocked_by_name`
- `block_reason`

同时在 `events` 中追加：

- `tool_preflight`
- `tool_blocked`
- `tool_after_hook`

这样 transcript、session snapshot 与运行态 event stream 三个面都能复盘工具链行为。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/orchestration/test_agent_runtime.py` | plugin block / policy block / double inject（3 个） | 插件阻断、策略阻断、plugin+policy 双重注入顺序、tool metadata、runtime events |
| `test/extensions/test_plugin_runtime.py` | hook/block helper（1 个） | 插件 manifest 的 deny_prefixes / before_hooks / after_hooks 被 helper 正确暴露 |
| `test/extensions/test_hook_policy_runtime.py` | hook/block helper（1 个） | policy runtime 的 merged fallback 与 block helper 正常工作 |

## 回归结果

定向验证：

- `python -m unittest discover -s test/orchestration -p "test_agent_runtime.py" -v` → 28/28 OK
- `python -m unittest discover -s test/extensions -p "test_plugin_runtime.py" -v` → plugin helper 场景 OK
- `python -m unittest discover -s test/extensions -p "test_hook_policy_runtime.py" -v` → policy helper 场景 OK
- `python -m unittest discover -s test -v` → 199/199 OK
