## Step 9 实现记录：收口 session 与 planning 的公开边界

### 概要

Step 9 为相对稳定的 `session` 和 `planning` 两个领域补上唯一的公开 Facade 入口，消除外层模块对这两个领域内部实现的直接依赖。

### 完成内容

#### 1. 创建 `SessionManager` Facade（`src/session/session_manager.py`）

**职责**：作为会话子系统的唯一公开入口，统一暴露会话管理的全部能力。

**核心方法**：
- `save_session(snapshot)` - 保存会话快照到磁盘
- `load_session(session_id)` - 从磁盘加载会话快照
- `create_session_state(prompt)` - 创建新会话运行时状态
- `resume_session_state(messages, transcript)` - 从持久化数据恢复会话状态

**内部实现**：
- 包装 `AgentSessionStore`（文件持久化）
- 包装 `AgentSessionState`（运行时消息管理）
- 暴露 `AgentSessionSnapshot`（会话数据契约）

**关键设计**：
- 公开暴露 `directory` 属性以保持向后兼容性（用于测试）
- 所有复杂性被完全隐藏在 Facade 后

#### 2. 创建 `PlanningService` Facade（`src/planning/planning_service.py`）

**职责**：作为计划和任务子系统的唯一公开入口。

**核心功能分组**：
- 计划 API：`list_plan_steps()`, `get_plan_step()`, `update_plan()`, `clear_plan()`, `save_plan()`
- 任务 API：`list_tasks()`, `get_task()`, `create_task()`, `update_task()`, `start_task()`, `complete_task()`, `cancel_task()`, `save_tasks()`
- 同步 API：`sync_tasks_from_plan()` - 把计划步骤投影为任务

**内部实现**：
- 包装 `PlanRuntime`（计划状态管理）
- 包装 `TaskRuntime`（任务状态管理）
- 暴露数据契约：`PlanStep`, `PlanStepStatus`, `TaskRecord`, `TaskStatus`

**关键设计**：
- 通过 `plan_runtime` 和 `task_runtime` 属性暴露原始运行时对象（仅供高级集成使用）
- 提供高层次的业务操作接口

#### 3. 更新包导出（`__init__.py`）

**`src/session/__init__.py`**：只暴露 `SessionManager` + 稳定数据契约
```python
__all__ = [
    'SessionManager',
    'AgentSessionSnapshot',
    'AgentSessionState',
]
```

**`src/planning/__init__.py`**：只暴露 `PlanningService` + 稳定数据契约
```python
__all__ = [
    'PlanningService',
    'PlanStep',
    'PlanStepStatus',
    'TaskRecord',
    'TaskStatus',
]
```

#### 4. 全局依赖重构

**受影响的模块**：
- `agent/turn_coordinator.py` - 使用 `SessionManager` 替代 `AgentSessionStore`
- `agent/agent.py` - 字段从 `session_store` 改为 `session_manager`
- `agent/result_factory.py` - 使用 `session_manager.save_session()` 替代 `session_store.save()`
- `app/cli.py` - 注入参数改为 `session_manager_cls`
- `app/runtime_builder.py` - 使用 `session_manager_cls` 构造 Facade
- `app/chat_loop.py` - 使用 `session_manager_cls` 加载会话
- `app/query_service.py` - 访问 `session_manager.load_session()`

#### 5. 测试更新

**测试文件修改**：
- `test/app/test_query_service.py` - 使用 `SessionManager` 构造测试 agent
- `test/test_main.py` - Fake session store 改为 Fake session manager
- `test/test_main_chat.py` - 所有 patch 调用改为 mock `SessionManager`

**测试验证结果**：
- `test/app` - 4 tests passed ✓
- `test_main*.py` - 27 tests passed ✓
- `test_release_gate_docs.py` - 4 tests passed ✓
- **总计：35 tests passed**

### 架构改进点

1. **消除穿透式依赖** - 外层再也不会直接导入 `AgentSessionStore`、`TaskRuntime` 等内部实现
2. **语义清晰** - 使用 `SessionManager` 和 `PlanningService` 等有明确业务含义的名字
3. **变更隔离** - 内部实现的任何改变（如存储方式切换）不影响外层代码
4. **渐进式迁移** - 通过 Facade 暴露的属性实现向后兼容性（如 `SessionManager.directory`）

### 删除内容

无。Step 9 只添加 Facade，不删除任何旧文件（旧内部实现文件仍然保留，只是不再被外部直接使用）。

### 后续影响

- Step 10 将清理这些模块的内部组织结构（可能删除无用的内部模块或重新整理）
- 未来对 session 或 planning 的需求可以通过扩展 Facade 来满足，而不是突破层界

### 完成标准检查

- ✅ 外层模块只依赖 `SessionManager` 和 `PlanningService`
- ✅ 直接穿透 `store.py`、`task_runtime.py` 的调用被收口
- ✅ 所有测试通过（35 tests）
- ✅ 代码、测试、文档同步更新
