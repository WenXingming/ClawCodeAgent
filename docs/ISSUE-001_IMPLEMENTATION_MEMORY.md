# ISSUE-001 开发记忆（契约冻结）

## 1. 本次目标

完成 `FINAL_ARCHITECTURE_PLAN` 中的 ISSUE-001：

1. 核心契约对象定义。
2. 序列化/反序列化与兼容字段读取。
3. test 目录单元测试并通过。

## 2. 实现范围

### 已完成

1. 在根目录 `src` 新建契约模块：
   - `src/agent_types.py`
2. 新建包初始化文件：
   - `src/__init__.py`
3. 在根目录 `test` 新建测试：
   - `test/test_agent_types.py`
   - `test/__init__.py`

### 未实现（按计划故意延后）

1. OpenAI 客户端（ISSUE-002）。
2. 工具系统与 shell 安全集成（ISSUE-004/005）。
3. 主循环与会话持久化（ISSUE-006/007/008）。

## 3. 契约对象清单

本次在 `src/agent_types.py` 中实现了：

1. `TokenUsage`
2. `ModelPricing`
3. `BudgetConfig`
4. `OutputSchemaConfig`
5. `ModelConfig`
6. `ToolCall`
7. `AgentPermissions`
8. `AgentRuntimeConfig`
9. `ToolExecutionResult`
10. `AgentRunResult`

## 4. 设计决策（为了简单实用）

1. 采用单文件契约集中管理，降低初学者理解成本。
2. 所有 `from_dict` 都做温和容错，不因脏数据直接崩溃。
3. 兼容常见 camelCase / snake_case 字段，减少后续对接阻力。
4. 对关键对象保留 `to_dict`，便于后续 session 落盘与 API 传输。
5. 在少量关键位置加注释，解释为什么要兼容多字段名。

## 5. 测试与结果

执行命令：

```powershell
python -m unittest discover -s test -v
```

结果：

1. 运行 12 个测试用例。
2. 全部通过（OK）。

覆盖重点：

1. 默认值与构造行为。
2. 兼容字段读取（例如 `prompt_tokens`、`toolCalls`、`maxTotalTokens`）。
3. 序列化往返一致性。
4. 非法输入回退行为（避免异常中断）。

## 6. 后续开发入口（下一步）

建议进入 ISSUE-002：OpenAI-compatible 非流式客户端。

原因：

1. ISSUE-006 主循环依赖模型调用语义。
2. 先稳定客户端契约，再接工具执行会更顺。

推荐顺序：

1. ISSUE-002
2. ISSUE-004
3. ISSUE-005
4. ISSUE-006

## 7. 对后续自己的提醒

1. 任何新增契约字段都要同步：
   - `to_dict`
   - `from_dict`
   - 对应单元测试
2. 保持“简单可读”优先，不提前引入复杂抽象。
3. 每个 ISSUE 完成后都新增一份同类型记忆文档，减少上下文丢失。

## 8. 可读性维护补充（本次新增）

为满足“稳扎稳打、逐步理解”的开发方式，本次对 ISSUE-001 代码做了不改变行为的可读性维护：

1. 在文件开头增加了文件级注释说明：
   - `src/agent_types.py`
   - `test/test_agent_types.py`
2. 在 `src/agent_types.py` 增加了分区注释（helpers / contracts）。
3. 增加 `_first_present(...)` 以减少重复嵌套 `data.get(...)`，使兼容字段读取更直观。
4. 为主要 dataclass 添加简短说明注释，方便快速理解每个对象职责。
5. 在测试文件中增加了测试分组注释和测试类说明，明确每组测试意图。

回归测试结果（维护后再次执行）：

```powershell
python -m unittest discover -s test -v
```

结果：

1. 12/12 通过。
2. 与维护前行为一致，无回归。

## 9. 注释中文化维护（本次用户需求）

为提高阅读体验，本次已将 ISSUE-001 相关代码与测试中的英文注释/文档字符串改为中文：

1. `src/agent_types.py`
2. `test/test_agent_types.py`
3. `src/__init__.py`
4. `test/__init__.py`

说明：

1. 本次仅进行注释和文档字符串维护，不改变业务行为。
2. 注释中文化后再次执行单元测试，结果仍为 12/12 通过。

## 10. 命名与注释收敛（本次继续维护）

为进一步提升可理解性，本次在不改变序列化语义的前提下完成了命名与注释收敛：

1. `src/agent_types.py`
   - 将 token 使用统计主类统一命名为 `TokenUsage`。
   - 同步更新相关类型注解与调用点：
     - `__add__` 参数与返回类型
     - `from_dict` 返回类型
     - `ModelPricing.estimate_cost_usd(...)` 参数类型
     - `AgentRunResult.usage` 字段类型与 `default_factory`
     - `AgentRunResult.from_dict(...)` 的 usage 解析入口
2. 对 `src/agent_types.py` 中全部 dataclass 属性增加了简洁中文注释，统一为“字段职责短句”风格。
3. 同步更新测试与文档命名：
   - `test/test_agent_types.py`
   - `docs/FINAL_ARCHITECTURE_PLAN.md`

回归验证：

```powershell
python -m unittest test/test_agent_types.py -v
python -m unittest discover -s test -v
```

结果：

1. 两轮测试均通过。
2. 12/12 通过，无行为回归。


