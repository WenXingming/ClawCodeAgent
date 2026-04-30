# ClawCodeAgent 全局架构重构与编码规范白皮书
**版本**: 1.0 (Destructive Refactoring Edition)
**核心宗旨**: 破坏式重构、网关收敛、全面依赖注入、极致的深度优先阅读体验。
**警告**: 任何重构动作必须严格遵守本文件的所有规则，不可妥协，不要保留对旧代码的兼容性！

## Part 1: 架构红线与破坏原则（Architecture & Anti-Leak）
1. **绝对隔离的门面 (Strict Facade Pattern)**：
   - 每一个领域文件夹下，必须有且仅有一个严格命名的 `{module}_gateway.py`，暴露极简的 Gateway 类作为该模块唯一的门面。
   - 外部模块**绝对禁止**绕过此门面访问文件夹内的任何其他内部文件、类或函数。
2. **全面依赖注入 (Dependency Injection, IoC)**：
   - Gateway/Facade 所需的外部配置、环境上下文、或跨模块的基础设施接口，**必须**通过 `__init__` 方法注入。
   - 内部的底层工作类（Workers/Sub-managers），所需的依赖由 Gateway 在初始化时向下注入。
   - **严禁**在模块内部私自 `import` 全局单例或硬编码直接实例化外部依赖。所有控制权必须反转交给上层装配器（如 App/Builder）。
3. **零泄漏原则 (Zero Leaky Abstractions)**：
   - Facade 的入参和返回值，只能是原生类型或定义在 `src/core_contracts` 中的契约类。
   - 发现外部需要用到内部数据结构（特定状态/异常类），必须强行移动到 `core_contracts`，或在 Facade 内部进行数据转换与异常翻译。
4. **破坏式精简 (Destructive Simplification)**：
   - 大胆删除为了旧代码兼容而存在的冗余设计、过渡性状态和庞大的 if-else 分支。
   - 结合 Facade 的屏蔽作用和 DI 带来的解耦，用最清爽、最直接的代码重塑内部业务流程，拒绝“原样搬运”。

## Part 2: 核心排版原则 (Depth-First Call Chain)
对重构后的全新代码，必须严格按照“深度优先的串行顺序”排列方法：
1. **构造优先**：类的属性定义、`__init__` 放在最前面。
2. **公有接口聚合**：所有公有函数（Public API，不带下划线）紧随其后。
3. **深度优先的串行展开（核心法则）**：
   - 私有辅助函数**不**按层级分组，而是按**调用的先后顺序**紧跟在调用者后面。
   - 排列顺序严格遵循调用链：若 `A` 调用 `_B` 和 `_C`，`_B` 调用 `_D`，则排列为：`A -> _B -> _D -> _C`。
   - 目的：实现“看到一个函数调用，下一个函数就是它的定义”，阅读流连续不断。
4. **去重原则**：被多次调用的私有辅助函数，只排在**它第一次被调用的位置**。

## Part 3: 极致的文档与注释规范 (Documentation Rules)
1. **文件与类级注释**：文件顶部必须有 Docstring 说明核心职责；类定义下方说明用途、核心工作流，并**明确列出需要注入的核心依赖**。
2. **成员变量注释**：为 `__init__` 中的**每一个**成员变量补充清晰的行内注释（类型、含义、作用）。
3. **紧凑型函数注释**：每一个函数（含私有）必须包含如下标准且紧凑的 Docstring：
     ```python
     def _execute_tool(self, tool_name: str) -> 'ToolExecutionResult':
         """执行指定工具并返回标准化结果。
         Args:
             tool_name (str): 需要执行的工具名称
         Returns:
             ToolExecutionResult: 标准化的工具执行结果对象
         Raises:
             ToolNotFoundError: 当工具未在注入的注册表中找到时抛出
         """
         pass
     ```


# ClawCodeAgent 全局架构重构与编码规范白皮书
**版本**: 2.0 (Autonomous Destructive Edition)
**核心宗旨**: 破坏式重构、网关收敛、全面依赖注入、极致的深度优先阅读体验。

## Part 1: 架构红线与破坏原则
1. **绝对隔离的门面 (Strict Facade Pattern)**：每个领域文件夹下仅有 `{module}_gateway.py` 暴露极简 Gateway 类。绝对禁止外部绕过门面访问内部。
2. **全面依赖注入 (Dependency Injection, IoC)**：Gateway 所需外部依赖必须通过 `__init__` 注入，内部 worker 依赖由 Gateway 注入。严禁内部硬编码实例化或导入外部重量级依赖。
3. **零泄漏原则 (Zero Leaky Abstractions)**：Gateway 入参和返回值只能是原生类型或 `core_contracts` 契约。内部数据结构/异常必须被转换翻译或抽离到 `core_contracts`。
4. **破坏式精简 (Destructive Simplification)**：大胆删除兼容性旧代码、冗余设计和庞大 if-else，用最清爽直接的代码重塑流程，拒绝原样搬运。

## Part 2: 核心排版原则 (Depth-First Call Chain)
1. **构造优先**：属性定义、`__init__` 最前。
2. **公有接口聚合**：公有函数紧随其后。
3. **深度优先的串行展开（核心法则）**：私有辅助函数按**调用的先后顺序**紧跟在调用者后面（例如：`A -> _B -> _D -> _C`），不按层级分组。
4. **去重原则**：多次调用的私有函数，排在首次被调用处。

## Part 3: 极致的文档与注释规范
1. **类级与成员注释**：文件顶部、类定义下方需有注释（明确列出注入的核心依赖），`__init__` 内每个被注入的成员必须有行内注释。
2. **紧凑型函数注释**：每一个函数（含私有）必须包含标准的 Docstring（作用、Args、Returns、Raises），格式必须紧凑。