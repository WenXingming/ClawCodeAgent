# ISSUE-010 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/context/context_snipper.py` | 新建 | `SnipResult`、`ContextSnipper` 与 tombstone 剪裁逻辑 |
| `src/context/__init__.py` | 修改 | 导出 `SnipResult` 与 `ContextSnipper` |
| `src/orchestration/agent_runtime.py` | 修改 | 在 soft_over 时接入 `ContextSnipper.snip()`，并追加 `snip_boundary` 事件 |
| `docs/architecture/Architecture.md` | 修改 | ContextPkg 中补充 `context_snipper.py` 节点与依赖关系 |
| `test/context/test_context_snipper.py` | 新建 | 20 个 snip 单测 |
| `test/orchestration/test_agent_runtime.py` | 追加 | 2 个 soft_over / non-soft_over 集成测试 |
| `docs/architecture/FINAL_ARCHITECTURE_PLAN.md` | 追加 | ISSUE-010 实施决策归档 |

## 关键设计决策

### 1. snip 只由 `is_soft_over` 触发
`orchestration/agent_runtime.py` 在每轮 token preflight 后检查 `snapshot.is_soft_over`；
仅当软超限时才执行 `ContextSnipper.snip()`，避免在正常上下文压力下过早丢弃历史内容。

### 2. 保留区间固定为“前缀 system + 尾部最近 N 条”
`ContextSnipper.snip()` 先通过 `_count_prefix()` 跳过头部连续 `system` 消息，
再保护尾部 `compact_preserve_messages` 条最近消息；只有中间段消息允许被替换为 tombstone。

### 3. 候选规则偏向“可恢复的旧冗余内容”
`_is_snippable()` 只接受以下消息：

- `role='tool'` 的工具结果
- 带 `tool_calls` 的 assistant 消息
- `content` 超过 300 字符的 assistant 长输出

已是 tombstone 的消息通过 `<system-reminder>\nOlder ` 前缀识别，禁止二次 snip。

### 4. tombstone 必须保留协议字段，只替换内容
`_make_tombstone()` 用 `<system-reminder>` 包装简短预览，同时保留：

- `role`
- `tool_call_id`
- `name`
- `tool_calls`

这样 tool message 链路和 assistant tool_calls 引用不会断裂。

### 5. snip 接口直接接收裸消息列表并就地修改
`ContextSnipper.snip(messages: list[dict])` 不依赖 `AgentSessionState`，而是直接操作
`session.messages` 引用并返回 `SnipResult`。这让算法层与 session 容器解耦，单测也能直接覆盖。

## 测试覆盖（新增 +22）

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/context/test_context_snipper.py` | `_is_snippable` 候选判定（8 个） | tool、system、user、短/长 assistant、tool_calls assistant、阈值边界、tombstone 去重 |
| `test/context/test_context_snipper.py` | `_make_tombstone` 行为（5 个） | 保留协议字段、`<system-reminder>` 格式、tool_calls 保留、完整原文不泄漏、预览截断 |
| `test/context/test_context_snipper.py` | `ContextSnipper.snip` 中间段剪裁（7 个） | 空消息、无候选、middle snip、tail 保留、prefix 保留、重复 tombstone 跳过、tokens_removed 非负 |
| `test/orchestration/test_agent_runtime.py` | `test_snip_triggered_on_soft_over` | soft_over 时产生 `snip_boundary`，并记录 `snipped_count/tokens_removed` |
| `test/orchestration/test_agent_runtime.py` | `test_no_snip_when_not_soft_over` | 不触发 soft_over 时不会追加 snip 事件 |

## 回归结果

补文档完成后再次运行 `python -m unittest discover -s test -v`：

- 测试数：152
- 结果：全部 OK
