"""app 控制面的组合门面。

该门面封装 RuntimeBuilder 与 ChatLoop 的协作，
把 CLI 层从具体对象装配细节中完全隔离。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from app.chat_loop import ChatLoop
from app.runtime_builder import RuntimeBuilder
from core_contracts.outcomes import AgentRunResult
from core_contracts.session import AgentSessionSnapshot
from core_contracts.config import SessionPaths


@dataclass
class AppRuntimeFacade:
    """封装 app CLI 执行所需的运行时能力的门面。

    注入依赖：
        runtime_builder (RuntimeBuilder): 负责从 CLI 参数装配 Agent 与 SessionPaths。
        chat_loop (ChatLoop): 负责驱动多轮交互主循环。
    """

    runtime_builder: RuntimeBuilder  # RuntimeBuilder：负责从 CLI 参数装配 Agent 实例与会话路径。
    chat_loop: ChatLoop  # ChatLoop：负责驱动 agent / agent-chat / agent-resume 的多轮交互循环。

    def build_agent_from_args(self, args: argparse.Namespace):
        """根据命令行参数构造新会话代理实例与会话路径。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            tuple[Agent, SessionPaths]: 新构造的 Agent 实例与本次会话路径配置。
        Raises:
            ValueError: 当必填参数未提供时抛出。
        """
        return self.runtime_builder.build_agent_from_args(args)

    def build_resumed_agent(self, args: argparse.Namespace, *, session_id: str):
        """根据持久化快照构造恢复态代理与相关元数据。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象（覆盖项来源）。
            session_id (str): 待恢复的会话唯一标识。
        Returns:
            tuple[Agent, AgentSessionSnapshot, Path | None]: 构造好的 Agent、会话快照和加载目录。
        Raises:
            ValueError: 当快照不存在或损坏时抛出。
        """
        return self.runtime_builder.build_resumed_agent(args, session_id=session_id)

    @staticmethod
    def resolve_show_progress(args: argparse.Namespace) -> bool:
        """从命令行参数中解析 progress 显示开关。

        Args:
            args (argparse.Namespace): 已解析的命令行参数对象。
        Returns:
            bool: True 表示应向 stdout 输出 progress 事件。
        Raises:
            无。
        """
        return RuntimeBuilder.resolve_show_progress(args)

    def run_chat_loop(
        self,
        agent,
        *,
        current_session_id: str | None,
        current_session_directory: Path | None,
        pending_session_snapshot: AgentSessionSnapshot | None,
        show_progress: bool,
    ) -> int:
        """执行多轮交互聊天循环并返回退出码。

        Args:
            agent: 已构建好的 Agent 实例。
            current_session_id (str | None): 当前关联的会话 ID；None 表示新会话。
            current_session_directory (Path | None): 会话快照所在目录；None 时使用 agent 默认。
            pending_session_snapshot (AgentSessionSnapshot | None): 恢复模式下预先加载的快照。
            show_progress (bool): 是否在执行期间向 stdout 输出 progress 事件。
        Returns:
            int: 退出码，0 表示正常退出。
        Raises:
            无（内部所有已知异常已被捕获）。
        """
        return self.chat_loop.run(
            agent,
            current_session_id=current_session_id,
            current_session_directory=current_session_directory,
            pending_session_snapshot=pending_session_snapshot,
            show_progress=show_progress,
        )
