"""Session 模块唯一公开门面（Facade）。

SessionGateway 是 session 领域的唯一对外入口，负责：
  1. 接收 core_contracts.session_contracts 中的标准请求 DTO
  2. 将请求路由到 SessionStore（持久化链路）或 SessionStateRuntime（运行态链路）
  3. 将内部 SessionContractError 子类原样透传，其他未预期异常翻译为 SessionContractError

Gateway 本身不含任何业务运算——所有计算均委托给两个内部组件。
依赖构造完全由外部完成，通常通过 session.__init__.create_session_gateway 工厂注入。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core_contracts.session_contracts import (
    AgentSessionState,
    SessionContractError,
    SessionLoadRequest,
    SessionLoadResult,
    SessionSaveRequest,
    SessionSaveResult,
    SessionStateCreateRequest,
    SessionStateResumeRequest,
)

if TYPE_CHECKING:
    from .session_state import SessionStateRuntime
    from .session_store import SessionStore


class SessionGateway:
    """Session 域公开门面。

    对上层编排器暴露四个标准契约方法（save / load / create_state / resume_state）
    不再提供冗余便捷方法，调用方统一走标准契约接口。
    """

    def __init__(
        self,
        session_store: SessionStore,
        session_state: SessionStateRuntime,
    ) -> None:
        """初始化 SessionGateway，强制要求显式依赖注入。

        外部必须通过 create_session_gateway() 工厂或测试 Mock 提供两个内部组件；
        Gateway 自身不承担任何依赖装配逻辑。

        Args:
            session_store (SessionStore): 会话持久化组件，负责 ID 校验、文件 I/O 与编解码。
            session_state (SessionStateRuntime): 会话运行态组件，负责状态创建与恢复。
        Returns:
            None
        Raises:
            None
        """
        self._session_store: SessionStore = session_store
        # SessionStore: 承载所有持久化职责（校验 / 路径 / I/O / 编解码）。
        self._session_state: SessionStateRuntime = session_state
        # SessionStateRuntime: 承载运行态状态创建与恢复职责。

    @property
    def directory(self) -> Path:
        """返回当前快照存储根目录。

        Args:
            None
        Returns:
            Path: 快照文件存储的绝对根目录。
        Raises:
            None
        """
        return self._session_store.directory

    # ── 公有接口 ──────────────────────────────────────────────────────────────

    def save(self, request: SessionSaveRequest) -> SessionSaveResult:
        """将会话快照持久化到磁盘。

        Args:
            request (SessionSaveRequest): 包含待保存快照的标准请求契约。
        Returns:
            SessionSaveResult: 包含 session_id 与落盘路径的结果契约。
        Raises:
            SessionContractError: 持久化失败时抛出（含 ID 校验错误与 I/O 错误子类）。
        """
        try:
            saved_path = self._session_store.save(request.snapshot)
            return SessionSaveResult(
                session_id=request.snapshot.session_id,
                session_path=str(saved_path),
            )
        except SessionContractError:
            raise
        except Exception as exc:
            raise SessionContractError(f'会话保存失败: {exc}') from exc

    def load(self, request: SessionLoadRequest) -> SessionLoadResult:
        """从磁盘加载会话快照并反序列化。

        Args:
            request (SessionLoadRequest): 包含 session_id 的标准请求契约。
        Returns:
            SessionLoadResult: 包含 session_id 与反序列化快照的结果契约。
        Raises:
            SessionContractError: 加载失败时抛出（含文件缺失、格式损坏等子类）。
        """
        try:
            snapshot = self._session_store.load(request.session_id)
            return SessionLoadResult(session_id=snapshot.session_id, snapshot=snapshot)
        except SessionContractError:
            raise
        except Exception as exc:
            raise SessionContractError(f'会话加载失败: {exc}') from exc

    def create_state(self, request: SessionStateCreateRequest) -> AgentSessionState:
        """基于首条提示词创建全新的运行态会话状态。

        Args:
            request (SessionStateCreateRequest): 包含初始 prompt 的标准请求契约。
        Returns:
            AgentSessionState: 已初始化的运行态会话状态对象。
        Raises:
            SessionContractError: prompt 不合法时抛出（SessionValidationError 子类）。
        """
        try:
            return self._session_state.build_new(request.prompt)
        except SessionContractError:
            raise
        except Exception as exc:
            raise SessionContractError(f'会话状态创建失败: {exc}') from exc

    def resume_state(self, request: SessionStateResumeRequest) -> AgentSessionState:
        """从持久化消息与转录数据恢复运行态会话状态。

        Args:
            request (SessionStateResumeRequest): 包含消息与转录序列的标准请求契约。
        Returns:
            AgentSessionState: 恢复后的运行态会话状态对象。
        Raises:
            SessionContractError: 数据格式不合法时抛出（SessionValidationError 子类）。
        """
        try:
            return self._session_state.build_from_persisted(
                request.messages, request.transcript
            )
        except SessionContractError:
            raise
        except Exception as exc:
            raise SessionContractError(f'会话状态恢复失败: {exc}') from exc

