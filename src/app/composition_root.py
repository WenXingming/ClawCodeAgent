"""app 层统一 composition root。

本模块是 app 领域的纯内部实现，禁止外部直接导入。
AppCompositionRoot 作为唯一的组合根，集中装配 CLI 运行时依赖，
输出供 AppCLI 消费的单一门面 AppRuntimeFacade。
"""

from __future__ import annotations

from app.chat_loop import ChatLoop
from app.runtime_builder import RuntimeBuilder
from app.runtime_facade import AppRuntimeFacade
from interaction.interaction_gateway import InteractionGateway


class AppCompositionRoot:
    """集中装配 CLI 运行时依赖的组合根。

    该类只提供静态工厂方法，不持有任何实例状态。
    所有可测试的运行时依赖（openai_client_cls、agent_cls、session_manager_cls）
    均以参数形式传入，便于测试替换。
    """

    @staticmethod
    def build_runtime_facade(
        *,
        openai_client_cls,
        agent_cls,
        session_manager_cls,
    ) -> AppRuntimeFacade:
        """构造 AppCLI 所需的单一运行时门面。

        Args:
            openai_client_cls: 可注入的 OpenAI 客户端类型，须与 OpenAIClientGateway 兼容。
            agent_cls: 可注入的 Agent 类型，须与 AgentGateway 兼容。
            session_manager_cls: 可注入的会话管理器类型，须与 SessionGateway 兼容。
        Returns:
            AppRuntimeFacade: 含 RuntimeBuilder 与 ChatLoop 的 app 运行时门面。
        Raises:
            无。
        """
        runtime_builder = RuntimeBuilder(
            openai_client_cls=openai_client_cls,
            agent_cls=agent_cls,
            session_manager_cls=session_manager_cls,
        )
        chat_loop = ChatLoop(
            session_manager_cls=session_manager_cls,
            interaction_gateway=InteractionGateway(),
            chat_exit_commands=frozenset({'/exit', '/quit'}),
        )
        return AppRuntimeFacade(runtime_builder=runtime_builder, chat_loop=chat_loop)
