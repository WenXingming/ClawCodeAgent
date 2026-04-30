"""app 层统一 composition root。"""

from __future__ import annotations

from app.chat_loop import ChatLoop
from app.runtime_builder import RuntimeBuilder
from app.runtime_facade import AppRuntimeFacade
from interaction.interaction_gateway import InteractionGateway


class AppCompositionRoot:
    """集中装配 CLI 运行时依赖。"""

    @staticmethod
    def build_runtime_facade(
        *,
        openai_client_cls,
        agent_cls,
        session_manager_cls,
    ) -> AppRuntimeFacade:
        """构造 AppCLI 所需的单一运行时门面。"""
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
