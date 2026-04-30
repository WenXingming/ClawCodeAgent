"""context 包公开入口。

架构约定：
- 对外仅暴露 ContextGateway 与 create_context_gateway 工厂函数。
- 所有外部消费者必须通过本入口访问 context 治理能力；禁止跨包直接依赖内部子模块。
- 内部实现类型（Snipper、Compactor、BudgetProjector 等）仅允许通过
  context.context_gateway 模块路径访问（用于单元测试白盒场景）。
"""

from __future__ import annotations

from core_contracts.model import ModelClient

from .context_gateway import ContextGateway


def create_context_gateway(client: ModelClient | None = None) -> ContextGateway:
    """工厂函数：构造全部内部组件并通过依赖注入装配 ContextGateway。

    调用方只需传入可选的模型客户端；BudgetProjector、Snipper、Compactor 的实例化
    由本工厂统一负责，外部无需感知任何内部构件。

    Args:
        client (ModelClient | None): 可选模型客户端。为 None 时网关仅支持预算投影；
                                     compact 与 reactive compact 路径将在调用时抛出 RuntimeError。
    Returns:
        ContextGateway: 完整初始化的上下文治理网关实例。
    Raises:
        无。
    """
    from .budget_projection import BudgetProjector
    from .compactor import Compactor
    from .snipper import Snipper
    from .token_estimator import TokenEstimator

    estimator = TokenEstimator()
    return ContextGateway(
        client=client,
        budget_projector=BudgetProjector(token_estimator=estimator),
        snipper=Snipper(token_estimator=estimator),
        compactor=Compactor(client, token_estimator=estimator) if client is not None else None,
    )


__all__ = ['ContextGateway', 'create_context_gateway']
