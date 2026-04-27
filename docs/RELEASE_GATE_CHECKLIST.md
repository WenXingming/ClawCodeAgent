# 发布门禁清单

## 1. 使用方式

本清单用于发布前逐项确认自动化结果、人工 smoke 与文档状态是否达标。

自动化统一入口：

```powershell
powershell -ExecutionPolicy Bypass -File ./scripts/run_release_gate.ps1
```

## 2. 门禁项目

| 检查项 | 要求 | 结果 |
|--------|------|------|
| 全量回归 | `python -m unittest discover -s test -v` 通过 | [ ] |
| orchestration 回归 | `python -m unittest discover -s test/orchestration -v` 通过 | [ ] |
| CLI 主命令 smoke | `agent --help` / `agent-chat --help` / `agent-resume --help` 均返回 0 | [ ] |
| 文档校验 | `test_release_gate_docs.py` 通过 | [ ] |
| README / 测试矩阵 / demo 脚本一致 | 命令与文档无失效说明 | [ ] |
| 真实后端人工 smoke | 按 [DEMO_SCRIPT.md](DEMO_SCRIPT.md) 至少跑 1 轮 | [ ] |

## 3. 失败处理准则

1. 任一自动化失败：禁止发布，先修复后重跑完整门禁。
2. 人工 smoke 失败：禁止发布，先更新实现或文档，再重跑 smoke。
3. 文档和命令不一致：优先修正文档或脚本，避免带着失效指引发布。

## 4. 结果模板

```md
发布时间：
执行人：
分支：
提交：

自动化结果：
- full unittest:
- orchestration regression:
- CLI help smoke:
- docs validation:

人工 smoke：
- agent:
- agent-chat:
- agent-resume:
- QueryEngine:

结论：允许发布 / 阻断发布
备注：
```