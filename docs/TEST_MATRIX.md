# 测试矩阵

## 1. 目标

本矩阵用于收敛 ClawCodeAgent 当前发布前必须覆盖的自动化验证与人工 smoke 检查，避免测试入口散落在各 Issue 文档中。

## 2. 自动化矩阵

| 层级 | 风险面 | 当前自动化覆盖 | 命令入口 | 发布门禁 |
|------|--------|----------------|----------|----------|
| 单元 | core_contracts / session / agent / context / tools | 配置契约、序列化、预算闸门、上下文治理、基础工具 | `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v` | 必跑 |
| 集成 | Agent 主循环 | slash、resume、预算、plugin/policy、delegate_agent、QueryEngine | `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/agent -v` | 必跑 |
| 控制面 | CLI 入口与 chat loop | `agent` / `agent-chat` / `agent-resume` 参数面与交互循环 | `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_main*.py" -v` | 必跑 |
| 扩展 | plugin / policy / search / MCP / worktree | manifest 发现、状态持久化、真实适配、受管 worktree | `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -v` | 必跑 |
| 计划与流程 | task / plan / workflow | 任务状态机、计划同步、工作流执行记录 | `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/planning -v` | 必跑 |
| 文档校验 | release gate 文档与脚本 | 关键文件存在性、关键命令一致性 | `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_release_gate_docs.py" -v` | 必跑 |
| CLI smoke | 命令帮助与解析 | `agent` / `agent-chat` / `agent-resume` help | `powershell -ExecutionPolicy Bypass -File ./scripts/run_release_gate.ps1` | 必跑 |
| 人工演示 | 真实后端联调 | 交互式 agent、resume、delegate_agent、QueryEngine API | 见 [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | 发布前至少一轮 |

## 3. 高风险场景映射

| 高风险场景 | 主要测试文件 | 说明 |
|------------|--------------|------|
| prompt 过长治理链路 | `test/context/test_context_*.py` + `test/agent/test_agent.py` | 覆盖 preflight、snip、auto compact、reactive compact |
| 权限拒绝链路 | `test/tools/test_local_tools_shell.py` + `test/agent/test_agent.py` | 覆盖写文件权限、shell 权限与危险命令限制 |
| resume 累计预算链路 | `test/agent/test_agent.py` + `test/test_main_chat.py` | 覆盖 session 连续性、usage/tool_calls/turns 累计 |
| plugin / policy 冲突链路 | `test/extensions/test_plugin_runtime.py` + `test/extensions/test_hook_policy_runtime.py` + `test/agent/test_agent.py` | 覆盖 deny、hook、block 优先级 |
| delegate_agent 失败/跳过/预算终止 | `test/agent/test_delegation_service.py` + `test/agent/test_agent.py` + `test/orchestration/test_query_engine.py` | 覆盖 child failure、dependency_skipped、delegated_task_limit 和统计汇总 |

## 4. 推荐执行顺序

1. 运行 `scripts/run_release_gate.ps1`。
2. 若脚本全部通过，再按 [DEMO_SCRIPT.md](DEMO_SCRIPT.md) 执行人工 smoke。
3. 对外发布前，把结果记录到 [RELEASE_GATE_CHECKLIST.md](RELEASE_GATE_CHECKLIST.md) 的模板区域。