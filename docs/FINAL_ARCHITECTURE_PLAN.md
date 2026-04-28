# Claw Code Agent 最终架构计划与开发路线图

## 1. 文档目的

本文件是对 docs 目录四份架构文档的统一收敛版本，并结合当前原项目代码与测试现状形成可执行开发计划。

目标不是逐字复刻 npm 代码，而是建设一个可演示、可恢复、可治理、可扩展的 Python Coding Agent 系统。

## 2. 分析输入

本计划来自以下信息源的交叉验证：

1. docs 目录原有四份中文架构文档。
2. 原项目 README、PARITY_CHECKLIST、TESTING_GUIDE。
3. 当前核心源码主干：main、interaction、orchestration、planning、extensions、budget、context、session、tools。
4. 当前关键扩展与状态机：plugin、hook_policy、task、plan、workflow、search、mcp。
5. 当前关键测试：test/orchestration/test_local_agent.py、test/extensions/*、test/planning/*、test/test_main.py、test/test_main_chat.py。

## 3. 最终产品目标

### 3.1 总体目标

构建一个本地优先、OpenAI-compatible、具备多轮工具调用与工程治理能力的 CLI Coding Agent。

### 3.2 必须具备能力（Must Have）

1. Agent Loop：模型回答与工具调用可循环收敛。
2. 工具安全：文件写入、shell、危险命令分级控制。
3. 会话恢复：session 持久化与 resume 连续执行。
4. 预算治理：token、cost、tool-call、model-call、turn 多维限制。
5. 上下文治理：snip、compact、prompt 预检与超长保护。
6. 控制面：CLI 子命令与高频 slash 命令。

### 3.3 建议增强能力（Should Have）

1. 插件与策略运行时：manifest 驱动别名、虚拟工具、hook、工具阻断。
2. 任务与计划运行时：plan-task 同步、依赖阻塞、可执行下一任务。
3. 外部能力运行时：search 或 MCP 至少一个完整打通。
4. 后台会话：任务可后台执行、查询日志、附着和终止。

### 3.4 可选进阶能力（Could Have）

1. worktree、workflow、team、remote trigger 等协同能力。
2. 更真实的 LSP 语义能力替代启发式实现。
3. 多代理更高级编排策略与更强可视化观测。

## 4. 当前代码基线架构（已验证）

### 4.1 分层视图

1. 入口层：`src/main.py` + `src/interaction/command_line_interaction.py`
2. 核心编排层：`src/orchestration/local_agent.py` 的 `LocalAgent`
3. 扩展能力层：`src/extensions/`（plugin / hook_policy / search / mcp）
4. 计划状态机层：`src/planning/`（task / plan / workflow）
5. 上下文与预算层：`src/context/`（token 估算、预算投影、snip/compact 上下文治理）+ `src/budget/`（`BudgetGuard` 五维执行闸门）；两层形成 `context` → `budget` 的单向依赖
6. 状态与持久化层：`src/session/session_state.py` + `src/session/session_snapshot.py` + `src/session/session_store.py`
7. 模型接入与共享契约层：`src/openai_client/openai_client.py` + `src/core_contracts/`

### 4.2 当前核心执行链

1. 解析 CLI 参数并构造 `ModelConfig`、静态运行策略契约组与 `BudgetConfig`。
2. 构建 `OpenAIClient` 与 `LocalAgent`，并注入运行配置与工具注册表。
3. 依据是否提供 `--session-id`，选择新会话 run 或 resume 路径。
4. 进入 turn loop：`BudgetContextOrchestrator` 处理 pre-model preflight/snip/compact 与 reactive compact 重试 -> model call。
5. 解析 tool calls，执行工具并写回 transcript。
6. 多处预算检查与 stop_reason 约束。
7. 结束后持久化 session，并支持后续 resume。

### 4.3 当前代码优势

1. 主循环完整，包含预检、压缩、恢复与预算。
2. 工具安全链路明确，含权限和命令安全检查。
3. `orchestration/planning/extensions` 边界已经落地，绝大多数能力继续采用 `from_workspace` + 本地状态文件范式。
4. 测试覆盖面广，关键流程已有端到端模拟测试。

### 4.4 当前边界与技术债

1. 部分能力仍偏本地模拟或启发式实现（例如 LSP 精度与部分 remote 深能力）。
2. 与完整 npm 生态仍有差距，尤其是更大命令面与桥接模式。
3. 部分高级特性需要更稳定的观测与回归基线支撑。

## 5. 统一目标架构（重构导向）

### 5.1 核心设计原则

1. 主循环只编排，不承担具体工具和 runtime 细节。
2. 工具统一协议化，所有工具走同一执行链。
3. 状态先定义 schema，再实现行为，保证恢复与向后兼容。
4. 安全与预算是默认前置约束，不是后置补丁。
5. 先闭环后扩展，避免早期功能面失控。

### 5.2 关键契约对象

1. `ModelConfig`
2. 静态运行策略契约组（`WorkspaceScope` / `ExecutionPolicy` / `ContextPolicy` / `ToolPermissionPolicy` / `SessionPaths`）
3. `BudgetConfig`
4. `TokenUsage`
5. `ToolCall`
6. `ToolExecutionResult`
7. `AgentRunResult`
8. `AgentSessionSnapshot`

## 6. 开发阶段计划（最终版）

### 阶段 P0：最小可运行闭环

目标：2 周内完成可运行主链路。

范围：

1. CLI `agent` 入口。
2. OpenAI-compatible 客户端。
3. 基础工具 `read_file`、`write_file`、`edit_file`、`bash`。
4. 最小权限门禁（write/shell）。
5. session 持久化与单次恢复。

验收标准：

1. 可完成一次读改写任务并输出结果。
2. 禁止权限下可稳定拒绝高危操作。
3. 能保存并恢复会话继续执行。

### 阶段 P1：工程可控性增强

目标：3 周内补齐稳定运行基础。

范围：

1. token budget 预检与 stop reason。
2. cost/tool/model/turn 预算闸门。
3. snip + compact + reactive compact。
4. 流式输出与截断续写。
5. 基础 slash 命令（help/context/status/permissions/tools）。

验收标准：

1. 长上下文任务不因 prompt 超长直接崩溃。
2. 预算超限可明确停止并给出原因。
3. 交互体验达到可持续对话与可追踪状态。

### 阶段 P2：扩展治理能力

目标：2-3 周引入插件和策略。

范围：

1. plugin manifest 发现与加载。
2. alias/virtual tool 能力。
3. before/after hook 注入。
4. 工具阻断和 policy budget override。
5. plugin state 持久化与 resume 恢复。

验收标准：

1. 插件可以新增虚拟工具并被主循环稳定调用。
2. policy 可阻断指定工具并反映到运行事件。
3. 插件状态在 resume 后保持一致。

### 阶段 P3：任务计划与流程化

目标：2 周建设可管理执行面。

范围：

1. task runtime：创建、更新、开始、完成、阻塞、取消。
2. plan runtime：步骤更新与 task 双向同步。
3. next-task 选择与依赖解锁。
4. workflow 基础执行记录。

验收标准：

1. 计划更新能自动映射为任务列表。
2. 依赖任务状态变化可驱动后续任务解锁。
3. 工作流执行结果可查询和回放。

### 阶段 P4：外部能力接入

目标：2-3 周打通外部信息与工具生态。

范围：

1. search runtime（provider 发现、激活、执行）。
2. MCP runtime（resources/tools 列举与调用）。
3. remote/worktree 其中一个做强打通。
4. 关键 slash 与 CLI 子命令对齐。

验收标准：

1. 外部搜索或 MCP 至少一个具备真实可用链路。
2. 关键控制命令覆盖常用开发场景。
3. 状态文件与历史记录可复现实验过程。

### 阶段 P5：质量门禁与发布

目标：2 周完成稳定性收口。

范围：

1. 单元、集成、端到端测试分层补齐。
2. 关键失败场景回归基线。
3. 文档收口：架构、测试、演示脚本。
4. 发布流程与版本约束。

验收标准：

1. 核心模块测试通过且可重复。
2. 主要 stop reason 与错误路径可回归。
3. 文档可直接支持演示与二次开发。

## 7. 测试策略与门禁

### 7.1 测试分层

1. 单元测试：工具、预算、序列化、权限、策略解析。
2. 集成测试：agent loop、slash 分流、resume 连续性。
3. 端到端测试：真实后端最小任务链路。

### 7.2 必测高风险场景

1. prompt 过长触发 preflight 与 reactive compact。
2. 权限拒绝路径（write/shell/destructive）。
3. 插件和 policy 同时作用下的工具阻断顺序。
4. resume 后预算和历史状态连续性。
5. delegate_agent 下子任务失败、依赖跳过与预算终止。

### 7.3 质量门禁

1. 所有高风险路径必须有自动化测试。
2. 每个阶段至少保留一组可演示命令。
3. 发布前执行 full tests + 关键 smoke tests。

## 8. 风险与缓解

1. 风险：功能扩展过快导致主循环复杂失控。
2. 缓解：严格按阶段推进，核心循环接口稳定优先。
3. 风险：上下文治理不足导致长会话退化。
4. 缓解：预检、压缩、停止条件和事件观测一起上。
5. 风险：工具安全漏洞带来破坏性行为。
6. 缓解：默认最小权限、危险命令阻断、策略双重闸口。
7. 风险：runtime 状态不一致导致 resume 漂移。
8. 缓解：状态 schema 固化、版本字段、回放测试。

## 9. 建议实施节奏（参考）

1. 第 1-2 周：P0。
2. 第 3-5 周：P1。
3. 第 6-8 周：P2。
4. 第 9-10 周：P3。
5. 第 11-13 周：P4。
6. 第 14-15 周：P5。

## 10. 最终交付清单

1. 可运行 CLI Agent 主程序。
2. 可恢复会话系统与状态文件。
3. 工具权限和预算治理链路。
4. 至少一条外部能力链路（search 或 MCP）。
5. 阶段化测试套件与回归脚本。
6. 架构文档、测试文档、演示流程文档。

## 11. 一句话结论

最终方向是“以可治理主循环为核心，以 manifest 驱动 runtime 扩展，以可恢复与可观测作为工程底座”的本地 Python Coding Agent 平台，而不是单次问答工具。

## 12. 可直接执行的任务清单（Issue 模板版）

说明：以下任务按编号执行，不按周拆分。每条都给出可直接创建 Issue 的模板内容。

### 12.1 通用 Issue 模板（复制用）

```md
## 背景

## 目标

## 范围

## 非范围

## 前置依赖

## 实施步骤
1.
2.
3.

## 验收标准（DoD）
1.
2.
3.

## 测试用例
1.
2.
3.

## 交付物
1.
2.

## 风险与回滚
```

### 12.2 Issue 清单

#### ISSUE-001 配置对象与运行时契约冻结

类型：architecture

背景：保证后续模块实现围绕稳定数据契约推进，避免反复改 schema。

目标：冻结 `ModelConfig`、静态运行策略契约组、`BudgetConfig`、`TokenUsage`、`ToolExecutionResult`、`AgentRunResult`。

范围：

1. 统一字段命名与默认值。
2. 定义序列化/反序列化规范。
3. 文档化每个字段的语义与边界。

非范围：工具具体实现与模型调用逻辑。

前置依赖：无。

实施步骤：

1. 盘点当前类型对象及字段。
2. 明确缺省值、可空策略与向后兼容策略。
3. 输出契约文档并补充对应单元测试。

验收标准（DoD）：

1. 核心 dataclass 定义稳定并通过测试。
2. 序列化后可完整恢复对象语义。
3. 无破坏性字段歧义。

测试用例：

1. 默认配置构造测试。
2. 序列化往返一致性测试。
3. 非法字段容错测试。

交付物：

1. 契约定义代码。
2. 契约说明文档。
3. 单元测试。

风险与回滚：字段调整会影响后续模块，需通过版本字段兼容历史存档。

#### ISSUE-002 OpenAI-compatible 客户端非流式能力

类型：feature

背景：主循环依赖稳定模型调用能力。

目标：实现非流式 complete 调用，兼容 tool_calls 和 usage 解析。

范围：

1. 请求构造。
2. 响应解析。
3. 错误封装与异常语义统一。

非范围：流式 SSE。

前置依赖：ISSUE-001。

实施步骤：

1. 定义请求参数映射。
2. 解析 content、tool_calls、finish_reason、usage。
3. 对接假后端测试桩。

验收标准（DoD）：

1. 能返回完整 OneTurnResponse。
2. tool_calls 参数可正确解析为对象。
3. 错误时返回统一异常类型。

测试用例：

1. 正常文本响应。
2. tool_calls 响应。
3. usage 字段缺失/变化格式场景。

交付物：客户端非流式实现与测试。

风险与回滚：不同后端字段差异大，先兼容主流字段并记录降级行为。

#### ISSUE-003 OpenAI-compatible 客户端流式能力

类型：feature

目标：支持 stream 输出、增量内容和增量工具调用解析。

范围：SSE 读取、事件归一化、message_stop 和 usage 聚合。

非范围：UI 层渲染。

前置依赖：ISSUE-002。

实施步骤：

1. 实现流式行读取与 DONE 终止。
2. 解析 content_delta、tool_call_delta、usage。
3. 输出标准 StreamEvent。

验收标准（DoD）：

1. 内容增量拼接正确。
2. 工具调用参数增量可恢复完整 JSON。
3. 结束事件与 usage 一致。

测试用例：

1. 纯文本流。
2. 混合工具调用流。
3. 提前中断与异常流。

交付物：流式客户端与测试。

#### ISSUE-004 基础工具集与执行上下文

类型：feature

目标：实现 `list_dir/read_file/write_file/edit_file` 与统一执行上下文。

范围：ToolRegistry、ToolExecutionContext、路径解析、输出截断。

非范围：bash 与远程工具。

前置依赖：ISSUE-001。

实施步骤：

1. 定义 LocalTool 协议。
2. 实现四个基础文件工具。
3. 增加统一错误分类。

验收标准（DoD）：

1. 四个工具可被主循环调用。
2. 路径越界禁止。
3. 错误信息结构化。

测试用例：正常读写、替换失败、路径越界。

交付物：工具代码与测试。

#### ISSUE-005 Shell 工具与安全策略

类型：security

目标：完成 bash 工具与危险命令分级控制。

范围：shell 权限、destructive 检测、超时、流式输出。

非范围：跨机 shell。

前置依赖：ISSUE-004。

实施步骤：

1. 接入 shell 权限判断。
2. 接入危险命令识别。
3. 输出 stdout/stderr 增量事件。

验收标准（DoD）：

1. 默认禁用 shell。
2. 危险命令在 unsafe=false 下被阻断。
3. 流输出可回放。

测试用例：安全命令、危险命令、链式命令、超时命令。

交付物：bash 工具、安全规则、测试。

#### ISSUE-006 LocalAgent 最小闭环

类型：feature

目标：实现 run 主循环（模型调用 -> 工具执行 -> 再调用 -> 收敛）。

范围：turn loop、max_turns、tool_calls 回填。

非范围：resume 与压缩。

前置依赖：ISSUE-002、ISSUE-004、ISSUE-005。

实施步骤：

1. 构建 session 与初始消息。
2. 调模型并解析 tool_calls。
3. 执行工具并继续下一轮。

验收标准（DoD）：

1. 可完成一次读-改-总结链路。
2. 达到停止条件后返回 AgentRunResult。
3. transcript 完整可追踪。

测试用例：无工具、单工具、多工具轮次。

交付物：最小 agent loop 与测试。

#### ISSUE-007 会话持久化与基础恢复

类型：feature

目标：完成 session save/load 与核心状态落盘。

范围：`AgentSessionSnapshot`、session 目录规范、序列化兼容。

非范围：复杂 replay 提示。

前置依赖：ISSUE-006。

实施步骤：

1. 定义落盘结构。
2. 接入 run 结束自动保存。
3. 提供 load 接口。

验收标准（DoD）：

1. 每次 run 都产出 session 文件。
2. 能从文件恢复基础消息和配置。
3. usage/cost 字段不丢失。

测试用例：保存读取一致性、缺失字段容错、损坏文件处理。

交付物：session/store 实现与测试。

#### ISSUE-008 Resume 连续执行与状态继承

类型：feature

目标：实现 resume(prompt, stored_session) 的连续执行语义。

范围：消息恢复、预算继承、plugin state 恢复。

非范围：跨版本迁移工具。

前置依赖：ISSUE-007。

实施步骤：

1. 注入持久化消息恢复 session。
2. 恢复 usage/cost/tool_calls/model_calls 基线。
3. 执行新 prompt 并继续保存同 session。

验收标准（DoD）：

1. resume 后上下文连续。
2. 预算累计正确。
3. 生成结果 session_id 不漂移。

测试用例：普通 resume、预算边界 resume、插件状态 resume。

交付物：resume 路径与测试。

**实施决策（已落地）**

- `AgentSessionState.from_persisted(messages, transcript, tool_call_count)` 负责恢复运行态；transcript 为空时从 messages 生成最小回退。
- `LocalAgent.resume(prompt, stored_session)` 严格继承 `stored_session.model_config` 与 `runtime_config`；usage/turns/tool_calls 从历史基线累计；cost = 历史成本 + 本次 delta，避免历史计费策略变化造成偏差。
- run/resume 共用 `_execute_loop` 私有方法，stop_reason 行为一致。
- CLI 新增 `--session-id` 参数；有 session_id 时走 load + resume，无时走原有 run 路径。
- `AgentSessionStore.load()` 现在对 FileNotFoundError 也抛 ValueError，main 统一 except 即可。
- **本期不实现 plugin state 恢复**，延后至 ISSUE-014/016 插件 runtime 一并处理。

#### ISSUE-009 Token Budget 预检

类型：feature

目标：在每次模型调用前做 prompt 长度预算评估。

范围：soft/hard limit、输出预留、schema 预留、聊天开销估算。

非范围：真实 tokenizer 全家族精确实现。

前置依赖：ISSUE-006。

实施步骤：

1. 计算 projected_input_tokens。
2. 计算 soft/hard input limit。
3. 返回 preflight 结果给主循环。

验收标准（DoD）：

1. 可在调用前检测超长风险。
2. hard overflow 直接阻断调用。
3. soft overflow 触发后续治理策略。

测试用例：不过限、软超限、硬超限。

交付物：token_budget 实现与测试。

**实施决策（已落地）**

- 新建 `src/context/` 子包，导出 `ContextTokenBudgetSnapshot`、`ContextTokenEstimator`、`ContextTokenBudgetEvaluator`，并统一从包级导出 `ContextSnipper` 与 `ContextCompactor` 等上下文治理对象。
- token 估算使用 **char/4 启发式**（1 token ≈ 4 chars），每条消息加 4 token 结构开销，工具 schema 序列化后按 char/4 计；如需精确计数可替换内部实现，公共接口不变。
- 常量：`OUTPUT_RESERVE_TOKENS=4096`（输出预留），`SOFT_BUFFER_TOKENS=13000`（auto-compact 触发缓冲）。
- 硬超限 `is_hard_over`：`projected > hard_limit - output_reserve` → `stop_reason='token_limit'`。
- 软超限 `is_soft_over`：`projected > hard_limit - output_reserve - soft_buffer` → 供 ISSUE-010/011 的 snip/compact 读取。
- **ISSUE-009 同时实现全部 BudgetConfig 维度的闸门**（用户决策）：除 token 外还包括 cost / tool_calls / model_calls / session_turns，均在 `_execute_loop` 的各预定位置插入，每次触发都追加 `budget_stop` event。
- token_budget 预检始终追加 `token_budget` event（含 `projected / is_hard_over / is_soft_over`），即使未超限也记录，供后续模块观测上下文压力。
- cost 闸门在模型调用前检查累计成本（`cost_baseline + estimate_cost_usd(usage_delta) >= max_total_cost_usd`）；`max_total_cost_usd=0.0` 时首轮即触发。
- tool_calls 闸门在每次 `append_tool_result` 后检查，多工具 response 中触发时后续工具不再执行。
- model_calls 闸门在每轮最前（session_turns 之后）检查，首轮成功 +1 后第 2 轮开始前拦截。
- session_turns 闸门在每轮最前检查 `turns_offset + turns_this_run > max_session_turns`，resume 场景下 offset 已满时第一轮即触发。

#### ISSUE-010 Snip 上下文剪裁机制

类型：feature

目标：先行轻量剪裁老消息，降低 prompt 压力。

范围：snip 候选规则、替换为 tombstone 摘要、事件记录。

非范围：摘要模型调用。

前置依赖：ISSUE-009。

实施步骤：

1. 定义可 snip 消息规则。
2. 执行替换并记录来源元数据。
3. 在 turn loop 开始阶段接入。

验收标准（DoD）：

1. token 压力下降可观测。
2. 不破坏最近工作上下文。
3. replay 能看到 snip 痕迹。

测试用例：候选为空、剪裁成功、多轮连续剪裁。

交付物：snip 逻辑与测试。

**实施决策（已落地）**

- `src/context/context_snipper.py` 提供 `ContextSnipper` 与 `SnipResult`；公开入口为 `ContextSnipper.snip(messages, *)`，接口直接接收裸 `list[JSONDict]` 并就地修改，不依赖 `AgentSessionState` 容器。
- 触发条件固定为 `ContextTokenBudgetEvaluator.evaluate(...).is_soft_over`；snip 发生在 turn loop 每轮 token preflight 之后、`token_budget` event 记录之前。
- 保留区间固定为“前缀连续 `system` 消息 + 尾部 `compact_preserve_messages` 条最近消息”；仅中间段候选允许被替换。
- 可 snip 候选限定为：`role='tool'`、带 `tool_calls` 的 assistant 消息、或 `content` 长度超过 300 字符的 assistant 消息；已是 tombstone 的消息通过 `<system-reminder>\nOlder ` 前缀识别并跳过。
- tombstone 内容统一写成 `<system-reminder>` 摘要块，仅替换 `content`；同时保留 `role / tool_call_id / name / tool_calls` 等协议字段，避免工具调用链断裂。
- snip 成功时追加 `snip_boundary` event，记录 `snipped_count` 与 `tokens_removed`；随后重新计算 token snapshot，使同轮 `token_budget` event 反映 snip 后状态。

#### ISSUE-011 Compact 与 Reactive Compact

类型：feature

目标：实现摘要压缩与 prompt-too-long 异常重试压缩。

范围：compact prompt、summary 边界消息、reactive retry。

非范围：多种摘要策略并行选择。

前置依赖：ISSUE-009、ISSUE-010。

实施步骤：

1. 实现 `ContextCompactor.compact()`。
2. 主循环接入 auto compact。
3. backend prompt-too-long 触发 reactive compact retry。

验收标准（DoD）：

1. 压缩后可继续任务，不丢主目标。
2. 遇到超长错误可自动尝试恢复。
3. 压缩元数据完整可追踪。

测试用例：自动压缩、反应式压缩、压缩失败回退。

交付物：compact 子系统与测试。

**实施决策（已落地）**

- 新建 `src/context/context_compactor.py`，导出 `CompactionResult / ContextCompactor`；`compact()`、`is_context_length_error()` 与 `should_auto_compact()` 统一收敛到 compactor 对象上，作为 context 子包的第二层治理能力。
- auto compact 触发条件采用 `ContextPolicy.auto_compact_threshold_tokens`，**不复用** `is_soft_over`；`is_soft_over` 继续仅作为 ISSUE-010 snip 的信号。
- compact 的消息布局复用 snip 的保留规则：前缀连续 `system` 消息不参与压缩，尾部 `compact_preserve_messages` 条最近消息保留，仅中间段被替换为 `compact boundary + compact summary` 两条 system reminder。
- compact 摘要调用显式禁用工具（`tools=[]`），只要求模型输出纯文本摘要；compact 写回消息只保留标准 OpenAI message 字段，不向模型发送自定义 metadata。
- reactive compact 仅在 `OpenAIResponseError` 呈现 prompt-too-long / context-length 类语义时触发；首版每轮最多重试 2 次，每次将 `preserve_messages` 逐步收紧到 1。
- compact 产生的模型调用也计入 `usage_delta` 与 `model_call_count`，因此可能影响同轮后续的 cost/model_calls 预算检查；预算耗尽时仍走既有 `budget_stop` / `backend_error` 语义，不新增 stop_reason。
- 运行事件新增 `compact_boundary`、`reactive_compact_retry` 与 `compact_failed`，用于观察 auto compact、reactive compact 及失败退化路径。

#### ISSUE-012 Slash 命令框架与高频命令

类型：feature

目标：实现 slash parse/dispatch，并先支持高频命令。

范围：`/help` `/context` `/status` `/permissions` `/tools` `/clear`。

非范围：全部 npm 命令镜像。

前置依赖：ISSUE-006。

实施步骤：

1. 解析 slash 命令。
2. 建立命令规格与 handler 注册。
3. 区分本地处理与继续 query。

验收标准（DoD）：

1. 命令可被正确分发。
2. 本地命令不触发模型调用。
3. 输出格式统一。

测试用例：有效命令、未知命令、继续 query 场景。

交付物：slash 框架与命令实现。

实现落地决策（2026-04-24）：

- 当前稳定入口为 `src/interaction/slash_commands_interaction.py`，集中承载 slash parse、命令规格注册和高频本地命令。
- slash 分流发生在 `LocalAgent.run/resume` 把 prompt 写入 `AgentSessionState` 之前，从根上保证本地命令不污染 `messages` 与 `transcript`。
- 本地 slash 命令统一返回 `stop_reason='slash_command'`，并写入 `slash_command` event；只记录 event，不写入 transcript。
- `/context` 复用 `ContextTokenBudgetEvaluator.evaluate(messages, tools, max_input_tokens)` 做本地上下文投影，展示当前 messages、transcript、tool_calls 与 projected tokens。
- `/clear` 采用 fork 语义：不覆盖旧 session 文件，而是生成新的 cleared `session_id` 并保存空会话快照；输出中同时提示旧 session_id 与新 session_id。
- 首版不改 `src/main.py` 参数面；slash 命令仍通过现有 `prompt` 入口传入，ISSUE-013 再处理 CLI 子命令扩展。
- 测试面拆为 `test/interaction/test_slash_commands.py` 单测与 `test/orchestration/test_local_agent.py` 集成测试，覆盖 `/help`、`/status`、`/clear` 的 no-model-call 路径。

#### ISSUE-013 CLI 命令面（agent/chat/resume）

类型：feature

目标：完善主 CLI 入口和常用子命令参数映射。

范围：`agent`、`agent-chat`、`agent-resume`。

非范围：所有扩展 runtime CLI 子命令。

前置依赖：ISSUE-006、ISSUE-008、ISSUE-012。

实施步骤：

1. 增强参数解析。
2. 映射到 runtime/model/budget config。
3. 完成 chat loop 与 resume 流程。

验收标准（DoD）：

1. 三个命令可稳定运行。
2. 参数覆盖权限与预算配置。
3. 错误提示可读。

测试用例：parser 测试、chat 多轮、resume 断点续跑。

交付物：CLI 子命令与测试。

实现落地决策（2026-04-24）：

- 新建 `src/interaction/` 包，把 CLI 与本地 slash 控制面放入同一组织边界；`main.py` 保留为极薄入口包装层，后续不再依赖旧 slash shim。
- CLI 采用强制子命令，固定为 `agent`、`agent-chat`、`agent-resume`，不再保留旧的顶层裸 prompt 用法；无子命令直接走 argparse 错误并返回非 0。
- `agent-resume` 与 `agent-chat --session-id` 采用“先加载存档，再按显式 CLI 参数覆盖”的策略，覆盖实现使用 dataclass `replace()`，避免 dict merge 打破契约边界。
- 参数面尽量对齐现有根仓库契约：模型、pricing、runtime、budget、permissions 都支持 CLI 覆盖；`output_schema` 与复杂系统提示拼装仍不纳入本期命令面。
- `agent-chat` 的本地退出命令使用 `.exit` / `.quit`；slash 命令不在 chat loop 内被截获，而是继续交给 runtime，保持 ISSUE-012 的控制面语义一致。
- chat loop 每轮都会追踪 `result.session_id` 与 `result.session_path`；因此 `/clear` 触发 fork 后，后续轮次会自然续接到新的 cleared session。
- 测试面拆为 `test/test_main.py`（子命令与覆盖路径）和 `test/test_main_chat.py`（交互循环与 `/clear` 会话切换）。

#### ISSUE-014 Plugin Runtime（manifest、alias、virtual）

类型：feature

目标：支持插件发现、别名工具与虚拟工具注册。

范围：manifest 发现、加载、校验、注册。

非范围：远端插件市场。

前置依赖：ISSUE-004。

实施步骤：

1. 定义 PluginManifest。
2. 实现 from_workspace 发现。
3. 注册 alias 与 virtual tool。

验收标准（DoD）：

1. 插件工具可进入 tool registry。
2. 名称冲突有明确处理策略。
3. 插件摘要可渲染。

测试用例：别名注册、虚拟工具执行、冲突处理。

交付物：plugin runtime 与测试。

#### ISSUE-015 Hook Policy Runtime（治理与预算覆盖）

类型：security

目标：实现 policy manifest 的信任、阻断、safe env、预算覆盖。

范围：deny_tools、deny_prefixes、before/after hook、budget override。

非范围：组织级远端策略下发。

前置依赖：ISSUE-001。

实施步骤：

1. 加载 policy 清单。
2. 合并多清单并定义优先级。
3. 暴露 hooks、safe_env、budget_overrides。

验收标准（DoD）：

1. deny 规则可生效。
2. safe env 正确传递到工具上下文。
3. budget override 可影响运行时预算。

测试用例：deny 命中、safe env、预算覆盖。

交付物：hook_policy runtime 与测试。

#### ISSUE-016 插件/策略接入工具执行链

类型：feature

目标：把 plugin/policy 前后钩子和阻断接入 tool pipeline。

范围：preflight message、block message、after-tool message、事件上报。

非范围：复杂策略编排 DSL。

前置依赖：ISSUE-014、ISSUE-015、ISSUE-006。

实施步骤：

1. 工具前接入 plugin/policy preflight。
2. 工具执行前阻断判断。
3. 工具后接入注入消息和 metadata。

验收标准（DoD）：

1. 阻断优先级明确且可追踪。
2. 元数据可用于后续审计。
3. 与原有工具执行兼容。

测试用例：插件阻断、策略阻断、双重注入。

交付物：主循环集成代码与测试。

#### ISSUE-017 Task Runtime（任务状态机）

类型：feature

目标：实现任务 CRUD 与依赖阻塞/解锁。

范围：create/update/start/complete/block/cancel/list/next。

非范围：跨仓库任务同步。

前置依赖：ISSUE-001。

实施步骤：

1. 定义 task 状态模型。
2. 实现本地持久化。
3. 实现依赖解析与 actionable 选择。

验收标准（DoD）：

1. 任务状态流转合法。
2. 依赖关系能正确阻塞和释放。
3. 文件持久化稳定。

测试用例：状态迁移、依赖阻塞、next tasks。

交付物：task runtime 与测试。

#### ISSUE-018 Plan Runtime（计划-任务同步）

类型：feature

目标：实现 plan 更新、清空及与 task 同步。

范围：PlanStep、update_plan、clear_plan、sync_tasks。

非范围：图形化计划编辑器。

前置依赖：ISSUE-017。

实施步骤：

1. 定义 PlanStep 与状态。
2. 实现 plan 存储。
3. 与 task runtime 建立同步。

验收标准（DoD）：

1. 计划可稳定更新与渲染。
2. 同步后任务列表与计划一致。
3. 清空操作同步清理任务。

测试用例：更新同步、依赖映射、清空同步。

交付物：plan runtime 与测试。

#### ISSUE-019 Workflow Runtime（流程运行记录）

类型：feature

目标：实现工作流定义读取、运行和历史记录。

范围：list/get/run/history。

非范围：分布式调度。

前置依赖：ISSUE-017。

实施步骤：

1. 发现 workflow manifest。
2. 实现 run 流程。
3. 持久化运行记录。

验收标准（DoD）：

1. 工作流可查询和运行。
2. 运行记录可回放。
3. 错误可诊断。

测试用例：发现、运行成功、运行失败。

交付物：workflow runtime 与测试。

#### ISSUE-020 Search Runtime（provider 与检索）

类型：feature

目标：支持 provider 发现、激活和真实检索。

范围：manifest/env 发现、active provider 状态、search 执行。

非范围：多 provider 并发融合排序。

前置依赖：ISSUE-006。

实施步骤：

1. 加载 provider profiles。
2. 支持 activate 与状态持久化。
3. 打通至少一种后端搜索。

验收标准（DoD）：

1. provider 切换可持久化。
2. 搜索结果结构化返回。
3. 网络异常可控处理。

测试用例：provider 发现、切换、查询失败重试。

交付物：search runtime 与测试。

#### ISSUE-021 MCP Runtime（资源与工具链路）

类型：feature

目标：实现 MCP 资源/工具发现与 stdio transport 调用。

范围：resources/list/read，tools/list/call，server profile。

非范围：远端 MCP 网关。

前置依赖：ISSUE-006。

实施步骤：

1. manifest 解析 server/resources。
2. stdio 协议调用。
3. 资源与工具渲染输出。

验收标准（DoD）：

1. 可列出并读取 MCP 资源。
2. 可列出并调用 MCP 工具。
3. 失败信息可追踪。

测试用例：资源读取、工具调用、无效 server。

交付物：mcp runtime 与测试。

#### ISSUE-022 Remote Runtime 与基础连接状态（本人注释：暂时可不做）

类型：feature

目标：支持 remote profile 发现、连接、断开与状态持久化。

范围：remote/ssh/teleport/direct-connect/deep-link 模式基础状态。

非范围：真实远端执行代理。

前置依赖：ISSUE-006。

实施步骤：

1. profile 清单加载。
2. connect/disconnect 状态机。
3. CLI/slash 状态查询。

验收标准（DoD）：

1. 多模式连接状态可持久化。
2. history 可追踪连接行为。
3. 未配置时提示友好。

测试用例：连接、断开、无效 profile。

交付物：remote runtime 与测试。

#### ISSUE-023 Worktree Runtime（受管工作树）

类型：feature

目标：实现 enter/exit managed worktree 与 cwd 切换。

范围：创建分支、进入工作树、退出保留或移除、历史记录。

非范围：复杂多 worktree 并发调度。

前置依赖：ISSUE-006。

实施步骤：

1. git 仓库与 common dir 检测。
2. enter 创建并切换。
3. exit 清理与回退 cwd。

验收标准（DoD）：

1. enter/exit 行为稳定且可回滚。
2. remove 时有变更保护策略。
3. 状态文件和历史完整。

测试用例：进入、退出保留、退出移除、脏工作树阻断。

交付物：worktree runtime 与测试。

实施落地决策（2026-04-27）：

- 新增 `src/extensions/worktree_runtime.py`，保持为**独立 runtime**，本期不直接接入主循环、CLI 或 slash 控制面；上层后续只需消费 `current_cwd`、`active_worktree()` 与历史记录即可完成集成。
- 持久化统一落在工作区 `.claw/`：当前使用 `.claw/worktree_state.json` 保存逻辑 cwd 与受管工作树列表，`.claw/worktree_history.json` 保存 enter/exit 历史事件。
- 默认 worktree 路径采用**仓库父目录下的 sibling 目录策略**：`<workspace-name>--wt--<sanitized-branch>`；若调用方显式传入相对路径，也按 `workspace.parent` 解析，避免默认把受管 worktree 放回仓库内部。
- runtime 只允许**一个 active managed worktree**；已退出保留或已移除的记录会继续留在状态文件中，满足“状态文件和历史完整”的验收要求，但本期不实现多 active worktree 调度。
- `exit_worktree(remove=True)` 仅执行 `git worktree remove`，**不删除分支**；这是刻意的保守策略，用来降低误删分支和回滚成本。脏工作树在 remove 前会先用 `git status --porcelain` 阻断。

#### ISSUE-024 delegate_agent 与 AgentManager 编排

类型：feature

目标：支持子任务委托、依赖批处理、lineage 追踪。

范围：delegate_agent 工具、group、child index、stop reason 汇总。

非范围：跨进程分布式代理。

前置依赖：ISSUE-006、ISSUE-017。

实施步骤：

1. 实现 delegate_agent 执行路径。
2. 引入 AgentManager 记录 lineage。
3. 增加依赖批次与失败策略。

验收标准（DoD）：

1. 子任务可串行/拓扑执行。
2. 依赖跳过行为明确。
3. 汇总报告包含子任务 stop reason。

测试用例：多子任务、依赖失败、resume 子会话。

交付物：delegate 与 manager 实现、测试。

实施落地决策（2026-04-27）：

- 新增 `src/orchestration/agent_manager.py`，把 child agent record、group、dependency batch 与 stop_reason 汇总收敛到**独立编排对象**，不把这类运行期 lineage 状态混入 TaskRuntime。
- `delegate_agent` 作为 **LocalAgent 内置工具** 暴露给模型，但实际执行走 LocalAgent 主循环里的专用分支，而不是完全复用 `LocalToolService.execute()`；这样可以保留 child/group runtime events，并对 `max_delegated_tasks` 给出专门的 stop_reason。
- child agent 仍使用同一个 `OpenAIClient`、同一组静态运行策略契约与 `AgentSessionStore`，但每个 child 都创建新的 `LocalAgent` 实例并共享同一个 `AgentManager`；本期以**串行执行 + 依赖拓扑分 batch** 作为“依赖批处理”的落地选择，不实现并发子代理。
- `max_delegated_tasks` 不并入 ISSUE-009 的通用五维 `BudgetGuard`，而是在 `delegate_agent` 执行前做专门检查；一旦超限，会保留 tool result，并把父代理 stop_reason 收敛为 `delegated_task_limit`。
- 当上游 child 失败时，下游依赖任务不会继续执行，而是被标记为 `dependency_skipped`；该状态会同时写入 AgentManager group summary、tool metadata 和 runtime events，供 ISSUE-025 统计层复用。

#### ISSUE-025 QueryEngine 门面与运行事件统计

类型：feature

目标：为上层交互提供统一 submit/stream/persist 与统计摘要。

范围：TurnResult、runtime event counters、summary 渲染。

非范围：Web UI。

前置依赖：ISSUE-006、ISSUE-008。

实施步骤：

1. 实现 runtime_agent 模式 submit。
2. 汇总 events、mutation、lineage、orchestration 统计。
3. 持久化 session 与 replay 支持。

验收标准（DoD）：

1. submit 与 stream_submit 行为一致。
2. 摘要报告可反映关键运行指标。
3. 与主循环数据不冲突。

测试用例：普通提交、流式提交、resume 后统计连续性。

交付物：query_engine 增强实现与测试。

实施落地决策（2026-04-27）：

- 新增 `src/orchestration/query_engine.py`，把 QueryEngine 明确定位为**LocalAgent 之上的轻量 facade**，不直接改 CLI 或再引入旧兼容端口；当前版本只实现 runtime agent 模式。
- `submit()` 与 `stream_submit()` 共用同一条 `_submit_runtime_message()` 路径：首轮走 `run()`，后续轮次按最近一次 `session_id` 自动 `load + resume()`，保证两种入口在会话连续性上行为一致。
- `TurnResult.usage` 定义为**相对上一轮累计值的增量 usage**，同时保留 `usage_total` 字段给上层做累计展示，避免 facade 使用方重复推导差分。
- 运行统计分两路采集：`events` 负责 runtime event / group status / child stop_reason / resumed child 计数；`transcript` 中 tool metadata 负责 mutation 与 lineage 统计，其中 mutation 当前按 `write_file` / `edit_file` 动作累计。
- `persist_session()` 不重复落盘，而是直接返回最近一次 `LocalAgent` 已保存的 `session_path`；这样 QueryEngine 不会和 session store 写入职责发生重叠。

#### ISSUE-026 测试矩阵与发布门禁收口

类型：quality

目标：建立可发布前执行的全链路验证。

范围：单测、集成、smoke 命令、失败场景回归、文档校验。

非范围：性能压测平台建设。

前置依赖：ISSUE-001 至 ISSUE-025。

实施步骤：

1. 汇总测试命令入口。
2. 建立关键失败场景回归套件。
3. 建立发布前检查清单与结果模板。

验收标准（DoD）：

1. 核心流程自动化测试稳定通过。
2. 高风险场景均有回归覆盖。
3. 文档与命令一致，无失效说明。

测试用例：

1. 主循环完整链路。
2. 权限拒绝链路。
3. prompt 过长治理链路。
4. resume 累计预算链路。
5. plugin/policy 冲突链路。

交付物：

1. 最终测试矩阵文档。
2. 发布门禁清单。
3. 演示脚本。

实施落地决策（2026-04-27）：

- ISSUE-026 不再新增一套平行测试框架，而是把当前已经存在的 `unittest` 入口、CLI smoke 与文档校验**收口成统一 release gate**；自动化入口固定为 `scripts/run_release_gate.ps1`。
- 自动化脚本只覆盖**环境无关**的检查：full tests、orchestration regression、CLI `--help` smoke、release docs validation；需要真实模型后端的交互式演示保留在 `docs/DEMO_SCRIPT.md` 中手工执行。
- 发布门禁文档拆分为三个职责清晰的文件：`docs/TEST_MATRIX.md` 负责覆盖面矩阵，`docs/RELEASE_GATE_CHECKLIST.md` 负责发布流程与结果模板，`docs/DEMO_SCRIPT.md` 负责人工演示脚本。
- 新增 `test/test_release_gate_docs.py` 作为文档校验自动化，确保测试矩阵、清单、演示脚本和 release gate 脚本存在且关键命令不漂移。

## 13. Issue 使用规则（执行建议）

1. 一次只推进一个 ISSUE 到完成态，避免并行修改主循环核心逻辑。
2. 每个 ISSUE 合并前必须附带对应测试与简短变更说明。
3. 若 ISSUE 涉及 schema 变更，必须同步更新序列化兼容策略。
4. 遇到高风险改动（预算、权限、压缩）先补测试再改实现。


