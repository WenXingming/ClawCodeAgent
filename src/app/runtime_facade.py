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
    """封装 app CLI 执行所需的运行时能力。"""

    runtime_builder: RuntimeBuilder
    chat_loop: ChatLoop

    def build_agent_from_args(self, args: argparse.Namespace):
        """构造新会话 agent 与会话路径。"""
        return self.runtime_builder.build_agent_from_args(args)

    def build_resumed_agent(self, args: argparse.Namespace, *, session_id: str):
        """构造恢复态 agent 与快照。"""
        return self.runtime_builder.build_resumed_agent(args, session_id=session_id)

    @staticmethod
    def resolve_show_progress(args: argparse.Namespace) -> bool:
        """解析 progress 开关。"""
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
        """执行交互式聊天循环。"""
        return self.chat_loop.run(
            agent,
            current_session_id=current_session_id,
            current_session_directory=current_session_directory,
            pending_session_snapshot=pending_session_snapshot,
            show_progress=show_progress,
        )
