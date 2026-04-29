"""Session 模块公开 API。

该模块只暴露 SessionManager Facade 及其必要的数据契约。
内部实现细节（SessionStore、SessionState、SessionSnapshot）被封装，外部不应直接导入。
"""

from .session_manager import (
    AgentSessionSnapshot,
    AgentSessionState,
    SessionManager,
)

__all__ = [
    'SessionManager',
    'AgentSessionSnapshot',
    'AgentSessionState',
]
