"""app 模块对外唯一公开网关。

本模块是 src/app 文件夹的唯一合法出口。
外部调用方（main.py、测试、远程接口）只允许通过 AppGateway 访问 app 领域的能力；
禁止直接导入 app.cli、app.chat_loop、app.runtime_builder、app.query_service 中的任何符号。

公开 API 摘要：
  - AppGateway.run_cli()          ── 驱动命令行交互会话
  - AppGateway.create_query_service() ── 创建程序化查询门面
  - QueryService                  ── 程序化查询门面类型（供类型标注使用）
  - QueryTurnResult               ── 单轮查询结果契约（来自 core_contracts）
  - QueryServiceConfig            ── QueryService 配置契约（来自 core_contracts）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core_contracts.app_contracts import QueryServiceConfig, QueryTurnResult

if TYPE_CHECKING:
    from app.query_service import QueryService as _QueryService


class AppGateway:
    """app 领域的统一对外网关。

    所有外部调用必须通过本类进行，不得绕过网关直接访问 app 内部模块。
    本类只暴露两类能力：
      1. CLI 会话入口（run_cli）
      2. 程序化查询服务工厂（create_query_service）
    """

    @staticmethod
    def run_cli(
        argv: list[str] | None = None,
        *,
        openai_client_cls,
        agent_cls,
        session_manager_cls,
    ) -> int:
        """执行 CLI 主入口并返回进程退出码。

        组装 AppCLI 并把命令行参数转发给它。该方法是整个进程的启动核心，
        所有核心依赖通过参数注入，便于测试时替换为 Fake 实现。

        Args:
            argv (list[str] | None): 命令行参数列表；为 None 时自动回退到 sys.argv。
            openai_client_cls: OpenAI 客户端类，须实现 OpenAIClientGateway 协议。
            agent_cls: Agent 类，须实现 Agent 协议。
            session_manager_cls: 会话管理器类，须实现 SessionGateway 协议。
        Returns:
            int: 进程退出码，0 表示成功，非 0 表示失败。
        Raises:
            无（内部异常在 AppCLI.main 内部捕获并折叠为退出码）。
        """
        from app.cli import AppCLI

        cli = AppCLI(
            openai_client_cls=openai_client_cls,
            agent_cls=agent_cls,
            session_manager_cls=session_manager_cls,
        )
        return cli.main(argv)

    @staticmethod
    def create_query_service(
        runtime_agent,
        *,
        config: QueryServiceConfig | None = None,
    ) -> '_QueryService':
        """基于现有 Agent 创建 QueryService 实例。

        QueryService 是面向程序化调用的查询门面，封装了 run / resume 调度、
        累计统计和流式输出等能力。

        Args:
            runtime_agent: 已构建好的 Agent 实例，须实现 run / resume / session_manager 接口。
            config (QueryServiceConfig | None): 可选配置；为 None 时使用默认配置。
        Returns:
            QueryService: 已与 runtime_agent 绑定的查询服务实例。
        Raises:
            无。
        """
        from app.query_service import QueryService

        return QueryService.from_runtime_agent(runtime_agent, config=config)


# 将稳定的数据契约类型通过网关模块重新导出，方便外部统一从此处导入。
__all__ = [
    'AppGateway',
    'QueryServiceConfig',
    'QueryTurnResult',
]
