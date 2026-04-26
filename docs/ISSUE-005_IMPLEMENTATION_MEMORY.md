# ISSUE-005 开发记忆（Shell 工具与安全策略）

## 1. 本次目标

完成 `FINAL_ARCHITECTURE_PLAN` 中 ISSUE-005 的最小可运行实现：

1. 接入 shell 权限判断。
2. 接入危险命令识别与阻断。
3. 提供 stdout/stderr 流式增量事件，并保证可回放。

## 2. 实现范围

### 已完成

1. 新增 `src/tools/bash_security.py`：
   - `SecurityBehavior`
   - `SecurityResult`
   - `split_command(...)`
   - `bash_command_is_safe(...)`
   - `check_shell_security(...)`
   - `get_destructive_command_warning(...)`
   - `is_command_read_only(...)`
2. 扩展 `src/tools/agent_tools.py`：
   - 新增 `bash` 工具注册。
   - 新增 `_ensure_shell_allowed(...)`。
   - 新增 `_run_bash(...)` 与 `_execute_shell_command(...)`。
   - 新增 `ToolStreamUpdate` 与 `execute_tool_streaming(...)`。
   - 新增 `_run_bash_stream(...)`，输出 `stdout/stderr/result` 三类事件。
3. 公开能力继续在 `src/tools/agent_tools.py` 暴露：
   - `ToolStreamUpdate`
   - `execute_tool_streaming`
4. 新增测试：
   - `test/tools/test_bash_security.py`
   - `test/tools/test_agent_tools_shell.py`

### 未实现（按计划故意延后）

1. 交互式 ASK/确认流程（本次只做 ALLOW/DENY）。
2. 远程 shell 与跨机执行。
3. 细粒度实时 IO 抽样（当前为命令完成后按块回放）。

## 3. 边界与约束沉淀

1. 默认安全策略：`allow_shell_commands=false` 时，任何 shell 调用都被拒绝。
2. 危险命令策略：`allow_destructive_shell_commands=false` 时，命中破坏性模式直接拒绝。
3. 执行超时策略：超时统一映射为 `tool_execution_error`。
4. 输出控制策略：最终结果仍受 `max_output_chars` 截断限制。
5. 回放策略：流式事件可通过按顺序拼接 `stdout/stderr` 片段重放。

## 4. 设计决策（简洁优先）

1. 安全规则与工具执行解耦：安全判定单独放在 `src/tools/bash_security.py`，便于测试和复用。
2. 保持统一错误模型：仍使用 `permission_denied` / `tool_execution_error` 两类。
3. 流式接口最小化：
   - `ToolStreamUpdate.kind` 仅保留 `stdout/stderr/result`。
   - 非 bash 工具通过 `execute_tool_streaming(...)` 直接回传 `result`。
4. 兼顾可测性：超时路径在 kill 后允许二次 communicate 失败并统一转换异常。

## 5. 验收标准映射（DoD）

DoD 来源：`docs/FINAL_ARCHITECTURE_PLAN.md`。

1. 默认禁用 shell：✅
   - 证据：`check_shell_security(...)` + `_ensure_shell_allowed(...)`。
2. 危险命令在 unsafe=false 下被阻断：✅
   - 证据：`bash_command_is_safe(...)` 和 destructive pattern；`execute_tool('bash', ...)` 返回 `permission_denied`。
3. 流输出可回放：✅
   - 证据：`execute_tool_streaming(...)` + `_run_bash_stream(...)` 输出 `stdout/stderr` 增量片段与最终 `result`。

## 6. 测试与结果

执行命令：

```powershell
C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/tools -p "test_agent_tools_shell.py" -v
C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/tools -p "test_bash_security.py" -v
C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v
```

结果：

1. `test/tools/test_agent_tools_shell.py`：6/6 通过。
2. `test/tools/test_bash_security.py`：8/8 通过。
3. 全量 `discover`：64/64 通过。

## 7. 对后续 ISSUE-006 的交接建议

1. 主循环如果需要工具增量 UI，可直接消费 `execute_tool_streaming(...)`。
2. 如果主循环只需最终结果，可继续使用 `execute_tool(...)` 保持最小改动。
3. 后续若引入用户确认流程，可在 `SecurityBehavior` 上扩展 ASK 分支并接入 `ask_user_runtime`。
