# REFAC Step 08 Implementation Memory

## 目标

Step 8 目标是把控制面从 `interaction/command_line_interaction.py` 和 `orchestration/query_engine.py` 迁入 `app/` 领域，形成清晰的 app facade 边界，并删除旧入口文件。

## 本次删除的旧设计

1. 删除 `src/interaction/command_line_interaction.py`。
2. 删除 `src/orchestration/query_engine.py`。
3. 删除 `test/orchestration/test_query_engine.py`（迁移到 app 目录）。

## 本次新增的模块

1. `src/app/cli.py`
2. `src/app/chat_loop.py`
3. `src/app/runtime_builder.py`
4. `src/app/query_service.py`
5. `src/app/__init__.py`
6. `test/app/test_query_service.py`

## 关键重构决策

1. CLI 控制面拆分为三段职责：
   - `AppCLI` 仅负责命令解析与分发。
   - `RuntimeBuilder` 负责模型/策略/预算/路径装配与 resume 覆盖逻辑。
   - `ChatLoop` 负责交互循环、progress 事件渲染、slash 输出面板和 session 状态推进。
2. 旧 `QueryEngine` 迁移为 `QueryService`，并保留原有 submit/stream/persist/summary 语义。
3. `main.py` 保持薄入口，只切换到 `app.cli.AppCLI`。
4. 为避免包初始化级联导致循环导入：
   - `agent/__init__.py` 与 `app/__init__.py` 改为 `__getattr__` 惰性导出。

## 测试改造

1. 新建 `test/app/test_query_service.py`，覆盖：
   - run -> resume 路径切换
   - stream summary 事件
   - delegate 统计与 lineage 聚合
   - mutation 统计
2. `test_main.py` / `test_main_chat.py` 保持 patch `main.Agent` 的测试策略，无需变更断言语义。
3. `test/test_release_gate_docs.py` 将 demo 关键字从 `QueryEngine` 更新为 `QueryService`。

## 文档同步

1. `docs/architecture/Architecture.md`：控制面与查询门面更新为 `app/*`。
2. `docs/release/TEST_MATRIX.md`：高风险链路测试路径更新为 `test/app/test_query_service.py`。
3. `docs/release/DEMO_SCRIPT.md`：示例导入切换到 `app.query_service.QueryService`。
4. `README.md`：QueryEngine 章节更新为 QueryService。

## 回归命令

1. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test/app -v`
2. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_main*.py" -v`
3. `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -p "test_release_gate_docs.py" -v`

## 结果

Step 8 已完成代码、测试、文档三项闭环：控制面与查询门面迁移到 `app/`，旧入口已删除，关键回归通过。

