# ISSUE-015 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/runtime/hook_policy_runtime.py` | 新建 | 实现 policy manifest 发现、trusted 过滤、deny/safe env/budget 合并 |
| `src/runtime/agent_runtime.py` | 修改 | `LocalCodingAgent` 初始化时加载 policy runtime，并应用 deny 过滤与 budget override |
| `src/tools/agent_tools.py` | 修改 | `ToolExecutionContext` 新增 `safe_env`，bash 子进程显式透传环境变量 |
| `test/runtime/test_hook_policy_runtime.py` | 新建 | 覆盖 trusted 合并、deny 过滤、safe env 与 budget override 聚合 |
| `test/runtime/test_agent_runtime.py` | 修改 | 增加 policy deny 与 budget override 的主循环集成测试 |
| `test/tools/test_agent_tools_shell.py` | 修改 | 增加 safe env 传入 bash 子进程的测试 |
| `README.md` | 修改 | 补充工作区 policy manifest 发现路径、字段示例与合并规则 |
| `docs/Architecture.md` | 修改 | 写回 hook policy runtime 在当前架构中的位置 |

## 关键设计决策

### 1. Hook Policy Runtime 只负责加载、合并与暴露，不直接执行 hooks
ISSUE-015 的实施步骤明确是“加载 policy 清单、合并优先级、暴露 hooks/safe_env/budget_overrides”。因此当前实现把 `before_hooks` / `after_hooks` 作为 runtime 数据暴露出来，但不在这里直接注入工具执行链；真正的 hook 执行放到 ISSUE-016 统一接入。

### 2. 采用工作区本地 manifest 约定，并增加 `trusted` 显式开关
当前实现默认扫描：

- `.claw/policies.json`
- `.claw/policies/*.json`

manifest 缺省 `trusted=true`；当 `trusted=false` 时会被跳过，并记录到 `skipped_manifests`。这样“信任”目标可以在本地 manifest 层明确表达，而不会和后续远端策略分发混在一起。

### 3. deny 规则通过过滤 tool registry 生效，而不是等到 ISSUE-016 再阻断
为了满足本期 DoD 中“deny 规则可生效”，当前实现选择在 `LocalCodingAgent.__post_init__()` 里直接基于 `deny_tools` 与 `deny_prefixes` 过滤最终 tool registry。这样 deny 对核心工具和 ISSUE-014 注册的插件工具都立即生效；模型侧也不会再看到被禁用工具。

### 4. budget override 在 agent 初始化阶段合并进 `runtime_config.budget_config`
`HookPolicyRuntime.apply_runtime_config()` 使用非空字段覆盖策略，把 policy 的 `BudgetConfig` 合并到运行配置里。这样预算覆盖从第一轮 preflight 就能生效，不需要等到工具链执行阶段。

### 5. safe env 进入 `ToolExecutionContext`，当前主要作用于 `bash`
`ToolExecutionContext` 新增 `safe_env` 字段，`build_tool_context()` 接收并复制它；`bash` 执行时用 `os.environ + safe_env` 显式构造子进程环境。当前其它工具不消费 `safe_env`，但上下文契约已为后续工具扩展留出位置。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test_hook_policy_runtime` | merge + filter（2 个） | trusted manifest 合并、untrusted 跳过、deny 过滤、safe env 与 budget override 聚合 |
| `test_agent_runtime` | policy budget / deny（2 个） | budget override 在首次模型调用前生效；deny 规则过滤实际 tool registry |
| `test_agent_tools_shell` | `test_bash_passes_safe_env_to_subprocess` | safe env 确实进入 bash 子进程 `env` |

## 回归结果

定向验证：

- `python -m unittest discover -s test/runtime -p "test_hook_policy_runtime.py" -v` → 2/2 OK
- `python -m unittest discover -s test/tools -p "test_agent_tools_shell.py" -v` → 7/7 OK
- `python -m unittest discover -s test/runtime -p "test_agent_runtime.py" -v` → 26/26 OK
- `python -m unittest discover -s test -v` → 195/195 OK


