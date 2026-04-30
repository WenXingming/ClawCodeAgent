"""session 模块公开入口。

本模块是 session 领域的唯一对外门面，核心职责：
  1. 通过 SessionGateway 统一暴露快照保存、加载、运行态状态创建与恢复四类能力。
  2. 屏蔽内部 SessionStore（持久化）与 SessionStateRuntime（运行态）实现细节。
  3. 外部代码只允许导入 SessionGateway，禁止直接访问 session 内部文件。

所有数据契约（请求 DTO、结果 DTO、异常类）定义在 core_contracts.session_contracts。
"""

from pathlib import Path

from .session_gateway import SessionGateway
from .session_state import SessionStateRuntime
from .session_store import SessionStore


def create_session_gateway(session_store_directory: Path | None = None) -> SessionGateway:
  """构造默认注入依赖并返回 SessionGateway。

  Args:
    session_store_directory (Path | None): 快照存储根目录；None 时使用默认路径。
  Returns:
    SessionGateway: 已装配完成的 session 门面实例。
  Raises:
    None
  """
  session_store = SessionStore(directory=session_store_directory)
  session_state = SessionStateRuntime()
  return SessionGateway(session_store=session_store, session_state=session_state)


__all__ = ['SessionGateway', 'SessionStore', 'SessionStateRuntime', 'create_session_gateway']

