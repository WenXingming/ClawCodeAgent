"""context 包公开入口。

架构约定：
- 对外仅暴露 ContextGateway，所有外部消费者必须通过本入口访问 context 治理能力。
- 禁止跨包直接依赖 context 子模块（budget_projection、snipper、compactor 等）。
- 内部实现类型（Snipper、Compactor、BudgetProjector 等）仅允许通过
  context.context_gateway 模块路径访问（用于单元测试白盒场景）。
"""

from .context_gateway import ContextGateway

__all__ = ['ContextGateway']
