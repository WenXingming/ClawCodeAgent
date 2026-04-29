# ISSUE-020 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/extensions/search_runtime.py` | 新建 | 实现 provider 发现、active provider 持久化、结构化检索和失败重试 |
| `test/extensions/test_search_runtime.py` | 新建 | 覆盖 provider 发现、切换持久化、结构化结果和查询失败重试 |
| `README.md` | 修改 | 补充 Search Runtime 的 manifest/env 发现、状态文件与查询示例 |
| `docs/architecture/Architecture.md` | 修改 | 把 search runtime 写回架构视图与运行时说明 |

## 关键设计决策

### 1. Search Runtime 保持独立，不直接接 agent 主循环
ISSUE-020 的目标是 provider 发现、激活和真实检索，不是立即把搜索能力注入 tool pipeline。因此当前实现把 `extensions/search_runtime.py` 保持为独立模块，后续再由控制面或工具链 issue 接入。

### 2. manifest 与状态文件统一回到 `.claw/`
当前实现使用：

- `.claw/search.json`
- `.claw/search/*.json`
- `.claw/search_state.json`

这样与 plugin / policy / task / plan / workflow 的工作区本地运行时保持一致，provider manifest 与 active provider 状态也都可以在仓库内自举。

### 3. provider 发现同时支持 manifest 与 env
当前 provider 来源有两类：

- 工作区 manifest
- 环境变量发现（当前实现为 `SEARXNG_BASE_URL`）

这样既能把稳定 provider 配置写进仓库，也能在本地临时通过环境变量注入搜索后端。

### 4. 当前只打通一个真实后端：SearxNG
规格只要求“至少一种后端搜索”，因此当前实现只接通 `searxng`。这样可以把本期范围控制在 provider/profile、active 状态和真实 HTTP 检索三件事上，而不提前引入多后端适配复杂度。

### 5. 搜索结果以结构化对象返回，失败走受控异常
`search()` 返回 `SearchResponse`，其中包含：

- `provider`
- `query`
- `attempts`
- `results`

单条结果为 `SearchResult`，包含 `title/url/snippet/provider_id/rank`。网络失败时不会返回混乱的原始异常，而是在重试耗尽后抛出 `SearchQueryError`，保留 `provider_id`、`attempts` 和错误文本，便于上层做稳定处理。

### 6. 重试语义放在 runtime 内部，而不是交给调用方拼装
查询失败重试是 ISSUE-020 的显式测试点，因此 `search()` 直接接收 `max_retries` 并在 runtime 内完成重试循环。这样 provider 选择、网络调用和错误聚合都在同一个切片里完成，调用方只消费最终结构化结果或受控失败。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/extensions/test_search_runtime.py` | discovery（1 个） | manifest provider 与 env provider 可被同时发现 |
| `test/extensions/test_search_runtime.py` | activate（1 个） | active provider 切换可持久化到 `.claw/search_state.json` |
| `test/extensions/test_search_runtime.py` | success（1 个） | SearxNG 查询返回结构化结果，包含 provider 和 attempts |
| `test/extensions/test_search_runtime.py` | retry failure（1 个） | 查询失败会按 `max_retries` 重试，并在耗尽后抛出受控异常 |

## 回归结果

定向验证：

- `python -m unittest discover -s test/extensions -p "test_search_runtime.py" -v` → 4/4 OK
