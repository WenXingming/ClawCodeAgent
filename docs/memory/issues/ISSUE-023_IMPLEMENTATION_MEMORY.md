# ISSUE-023 实现记忆文档

## 交付概览

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/extensions/worktree_runtime.py` | 新建 | 实现受管 worktree 的 enter/exit、git 检测、状态持久化与历史记录 |
| `test/extensions/test_worktree_runtime.py` | 新建 | 覆盖 enter、exit keep、exit remove 与 dirty worktree block |
| `README.md` | 修改 | 补充 Worktree Runtime 的能力说明、状态文件与代码示例 |
| `docs/architecture/Architecture.md` | 修改 | 把 worktree runtime 写入 extensions 包边界与架构视图 |
| `docs/architecture/FINAL_ARCHITECTURE_PLAN.md` | 修改 | 在 ISSUE-023 下记录已落地设计决策 |

## 关键设计决策

### 1. Worktree Runtime 先保持独立，不直接接主循环
ISSUE-023 的目标是把 git worktree 的 enter/exit、回退与历史记录做成稳定 runtime，而不是立即暴露成工具或 CLI 命令。因此当前实现落在 `src/extensions/worktree_runtime.py`，保持与 search/plugin/policy 一致的“工作区本地 runtime”定位，后续再由控制面 issue 接入。

### 2. 默认 worktree 路径采用 sibling 目录策略
当前默认路径不是仓库内部目录，而是仓库父目录下的 sibling 目录：

- `<workspace-name>--wt--<sanitized-branch>`

这样做是为了避免把另一个 worktree 再放回原仓库内部，减少文件遍历、路径越界判断和后续工具扫描时的歧义。若调用方传入相对路径，也按 `workspace.parent` 解析，保持这一策略一致。

### 3. 当前只维护一个 active managed worktree
规格明确把复杂多 worktree 并发调度排除在外，因此当前 runtime 只允许一个 `active` 记录。已退出保留或已移除的记录仍会留在状态文件中，满足状态可追溯要求，但不会在本期引入调度器或 worktree 池抽象。

### 4. remove 只删 worktree，不删分支
`exit_worktree(remove=True)` 的底层行为只调用 `git worktree remove`，不会额外删除分支。这是一个刻意保守的选择：先保证目录级回收和脏工作树保护，再把“是否清理分支”留给后续更高层控制面决定，避免本期把可回滚路径做窄。

### 5. 状态与历史分文件持久化
当前实现使用：

- `.claw/worktree_state.json`
- `.claw/worktree_history.json`

其中 state 负责保存当前逻辑 cwd、仓库检测结果和全部受管 worktree 记录；history 负责保存 enter/exit 事件。这样既便于后续控制面直接加载当前状态，也能独立回放操作历史。

### 6. 分支管理按仓库实际使用 `master` 作为集成分支
本仓库当前实际稳定分支是 `master`，因此本期实现按：

- `master`
- `feature/issue-023-worktree-runtime`
- merge 回 `master`

执行，而不机械套用计划文档中出现过的 `main` 字样。这样可以避免把 SOP 和仓库真实分支状态做成不一致。

## 测试覆盖

| 测试文件 | 测试方法/分组 | 验证点 |
|----------|---------------|--------|
| `test/extensions/test_worktree_runtime.py` | enter（1 个） | 创建新分支与新 worktree 后，`current_cwd` 切换到目标目录且状态文件落盘 |
| `test/extensions/test_worktree_runtime.py` | exit keep（1 个） | 退出保留会恢复逻辑 cwd，目录仍存在，状态文件中的记录变为 `exited` |
| `test/extensions/test_worktree_runtime.py` | exit remove（1 个） | 退出移除会删除底层 worktree 目录，并把 `exit_remove` 写入历史文件 |
| `test/extensions/test_worktree_runtime.py` | dirty block（1 个） | worktree 存在未提交变更时，`remove=True` 会被阻断，active 状态保持不变 |

## 回归结果

定向验证：

- `PYTHONPATH=src C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/extensions -p "test_worktree_runtime.py" -v` → 4/4 OK
