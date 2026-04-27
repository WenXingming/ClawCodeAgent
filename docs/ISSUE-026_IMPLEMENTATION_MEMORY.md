# ISSUE-026 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `docs/TEST_MATRIX.md` | 新建 | 收敛单测、集成、smoke、失败场景与文档校验矩阵 |
| `docs/RELEASE_GATE_CHECKLIST.md` | 新建 | 发布门禁清单与结果模板 |
| `docs/DEMO_SCRIPT.md` | 新建 | 真实后端联调前的人工演示脚本 |
| `scripts/run_release_gate.ps1` | 新建 | 统一发布前自动化入口 |
| `test/test_release_gate_docs.py` | 新建 | 校验 release gate 交付物存在且关键命令一致 |
| `README.md` | 修改 | 增加 release gate 入口与文档索引 |
| `docs/FINAL_ARCHITECTURE_PLAN.md` | 修改 | 在 ISSUE-026 下记录已落地设计决策 |

## 关键设计决策

### 1. 收口现有入口，而不是重造测试框架
ISSUE-026 的目标是发布前验证收口，不是再引入新的测试 runner。当前实现选择把现有 `unittest` 命令、CLI help smoke 和文档校验整合进 `scripts/run_release_gate.ps1`，最大限度复用已存在的测试资产。

### 2. 自动化只覆盖环境无关检查
真实模型后端、交互式对话与 API Key 依赖不适合被硬编码进通用 release gate。因此自动化脚本只跑：

- 全量回归
- orchestration 回归
- CLI `--help` smoke
- release gate 文档校验

需要真实后端的最终演示流程保留在 `docs/DEMO_SCRIPT.md` 中人工执行。

### 3. 文档职责拆分成三份稳定交付物
为了避免一个超长 README 同时承担矩阵、清单和演示脚本三种职责，当前拆成：

- `docs/TEST_MATRIX.md`
- `docs/RELEASE_GATE_CHECKLIST.md`
- `docs/DEMO_SCRIPT.md`

README 只保留索引入口。

### 4. 文档校验也进入自动化门禁
新增 `test/test_release_gate_docs.py`，校验这些交付物存在、脚本命令没有漂移、demo 覆盖 `agent-chat` / `agent-resume` / `QueryEngine`。这样发布门禁文档本身也具备回归保护。

### 5. 分支管理按仓库实际使用 `master` 作为集成分支
本仓库当前实际稳定分支是 `master`，因此本期实现按：

- `master`
- `feature/issue-026-release-gate`
- merge 回 `master`

执行，而不套用文档里偶尔出现的 `main` 文案。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/test_release_gate_docs.py` | artifact exists（1 个） | release gate 文档与脚本都已交付 |
| `test/test_release_gate_docs.py` | command consistency（1 个） | 测试矩阵与清单引用统一的关键命令 |
| `test/test_release_gate_docs.py` | demo coverage（1 个） | demo 覆盖 `agent-chat` / `agent-resume` / `QueryEngine` |
| `test/test_release_gate_docs.py` | script steps（1 个） | release gate 脚本包含 full tests、orchestration、docs validation 与 CLI smoke |

## 回归结果

定向验证：

- `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_release_gate_docs.py" -v` → 4/4 OK
- `powershell -ExecutionPolicy Bypass -File ./scripts/run_release_gate.ps1` → 通过后可作为统一发布前自动化入口