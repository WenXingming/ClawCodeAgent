# ISSUE-025 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/orchestration/query_engine.py` | 新建 | 实现 QueryEngine facade、TurnResult 与 runtime 统计累计 |
| `test/orchestration/test_query_engine.py` | 新建 | 覆盖 submit、stream_submit、persist、delegate stats 与 mutation 统计 |
| `README.md` | 修改 | 补充 QueryEngine 的使用方式与统计口径 |
| `docs/architecture/Architecture.md` | 修改 | 把 QueryEngine 写入 orchestration 包边界 |
| `docs/architecture/FINAL_ARCHITECTURE_PLAN.md` | 修改 | 在 ISSUE-025 下记录已落地设计决策 |

## 关键设计决策

### 1. QueryEngine 先做成独立 facade，不改 CLI
ISSUE-025 的目标是先给上层提供统一 submit / stream / persist 能力，而不是立即重构现有 CLI 入口。因此当前实现新增 `src/orchestration/query_engine.py`，保持为独立 facade，由后续控制面是否接入来决定上层入口迁移节奏。

### 2. 只支持 runtime agent 模式
当前 QueryEngine 不再保留旧的“伪会话端口”双轨逻辑，而是只包装 `LocalAgent`。这样 `submit()` 与 `stream_submit()` 都能直接复用已存在的会话恢复、tool pipeline、delegate_agent 和预算治理语义。

### 3. usage 同时暴露增量与累计
`LocalAgent` 返回的 `AgentRunResult.usage` 是会话累计值，因此 QueryEngine 额外计算：

- `TurnResult.usage`：相对上一轮的增量
- `TurnResult.usage_total`：当前累计值

这样既方便上层渲染单轮变化，也避免外部重复计算差分。

### 4. 统计来自两条稳定数据源
当前 runtime summary 不直接依赖内部私有状态，而是明确使用两条公开数据源：

- `events`：累计 runtime event / orchestration / child stop reason / resumed child 计数
- `transcript` 的 tool metadata：累计 mutation 与 lineage 统计

这使 QueryEngine 只消费稳定契约，不反向侵入 LocalAgent 内部实现细节。

### 5. persist_session 不重复写盘
`LocalAgent` 在每次 run/resume 结束时已经保存 session。当前 QueryEngine 的 `persist_session()` 只是返回最近一次提交的 `session_path`，不再自己写盘，避免 session store 职责重叠。

### 6. 分支管理按仓库实际使用 `master` 作为集成分支
本仓库当前实际稳定分支是 `master`，因此本期实现按：

- `master`
- `feature/issue-025-query-engine-facade`
- merge 回 `master`

执行，而不套用文档里偶尔出现的 `main` 文案。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/orchestration/test_query_engine.py` | submit/resume（1 个） | QueryEngine 首轮走 run，后续走 load + resume，并能返回已落盘 session_path |
| `test/orchestration/test_query_engine.py` | stream（1 个） | stream_submit 会输出 runtime_summary 和 message_stop |
| `test/orchestration/test_query_engine.py` | delegate stats（1 个） | delegate events、group status、child stop reason 与 lineage 统计被正确累计 |
| `test/orchestration/test_query_engine.py` | mutation stats（1 个） | write_file 这类工具结果会被累计到 runtime_mutation_counts |

## 回归结果

定向验证：

- `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/orchestration -p "test_query_engine.py" -v` → 4/4 OK
