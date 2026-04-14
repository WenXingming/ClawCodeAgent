# ISSUE-004 开发记忆（基础工具集与执行上下文）

## 1. 本次目标

完成 `FINAL_ARCHITECTURE_PLAN` 中 ISSUE-004 的最小可运行实现：

1. 实现 `list_dir/read_file/write_file/edit_file` 四个基础文件工具。
2. 提供统一执行上下文与注册表入口。
3. 实现路径越界拦截、写权限检查与结构化错误返回。

## 2. 实现范围

### 已完成

1. 在 `src/agent_tools.py` 增加工具层核心结构：
   - `ToolExecutionContext`
   - `AgentTool`
   - `build_tool_context(...)`
   - `default_tool_registry(...)`
   - `execute_tool(...)`
2. 完成四个基础工具实现：
   - `_list_dir(...)`
   - `_read_file(...)`
   - `_write_file(...)`
   - `_edit_file(...)`
3. 完成统一安全与错误处理：
   - `_resolve_workspace_path(...)` 负责工作区越界拦截
   - `_ensure_write_allowed(...)` 负责写权限拦截
   - `ToolPermissionError` 与 `ToolExecutionError` 统一映射到 `ToolExecutionResult.metadata.error_kind`
4. 在 `src/__init__.py` 导出 ISSUE-004 常用公开能力。
5. 新增 `test/test_agent_tools.py`，覆盖功能路径与安全路径。

### 未实现（按计划故意延后）

1. shell 工具与危险命令策略（ISSUE-005）。
2. 远程工具与跨机执行（后续 ISSUE）。
3. 工具执行流式事件与回放（ISSUE-005 范围）。

## 3. 边界与约束沉淀

1. 目录边界：所有路径必须位于 `ToolExecutionContext.root` 下。
2. 写权限边界：`write_file/edit_file` 必须 `allow_file_write=True` 才能执行。
3. 参数边界：参数类型采用严格校验，不做隐式类型转换。
4. 输出边界：`read_file/list_dir` 输出受 `max_output_chars` 截断约束。
5. 替换语义边界：`edit_file` 默认只替换首个匹配，`replace_all=true` 才替换全部。

## 4. 设计决策（简洁优先）

1. 用 `dict[str, AgentTool]` 作为注册表，不额外引入复杂容器类。
2. 工具处理函数统一返回 `(content, metadata)` 或 `content`，由 `AgentTool.execute()` 统一封装。
3. 路径防护优先采用 `Path.resolve() + relative_to(...)`，实现清晰且可审计。
4. 错误模型只保留两类执行错误：
   - `permission_denied`
   - `tool_execution_error`
5. 先确保本地文件工具稳定，再进入 shell 工具与流式扩展。

## 5. 验收标准映射（DoD）

DoD 来源：`docs/FINAL_ARCHITECTURE_PLAN.md`。

1. 四个工具可被主循环调用：✅
   - 证据：`default_tool_registry(...)` 提供四工具注册，`execute_tool(...)` 统一按名称调度。
2. 路径越界禁止：✅
   - 证据：`_resolve_workspace_path(...)` 对越界路径抛出 `ToolExecutionError`。
3. 错误信息结构化：✅
   - 证据：`AgentTool.execute()` 统一返回 `ToolExecutionResult`，并在 metadata 中标注 `error_kind`。

## 6. 测试与结果

执行命令：

```powershell
C:/ProgramData/anaconda3/python.exe -m unittest test/test_agent_tools.py -v
C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v
```

结果：

1. `test/test_agent_tools.py`：11/11 通过。
2. 全量 `discover`：50/50 通过。

关键覆盖：

1. 工具注册完整性。
2. 正常读写和按行读取。
3. 输出截断行为。
4. 写权限拒绝行为。
5. 路径越界拦截行为。
6. 未知工具的结构化错误。

## 7. 已知风险与后续关注

1. 当前仅支持文件工具，不含 shell 工具执行能力。
2. 当前工具层无增量事件输出能力，后续由 ISSUE-005 补齐。
3. `read_file` 采用 UTF-8 文本读取，二进制场景仍需后续策略扩展。

## 8. 对后续 ISSUE-005 的交接建议

1. 复用 `ToolExecutionContext` 的 `command_timeout_seconds` 与 `max_output_chars`。
2. 复用 `ToolExecutionResult` 的结构化错误约定，保持调用层兼容。
3. 先新增 shell 安全策略模块，再将 bash 工具接入 `default_tool_registry()`。
4. 流式输出可回放能力建议通过 `execute_tool_streaming(...)` 与事件结构补充实现。
