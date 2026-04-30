"""app 模块对外唯一公开网关。

本模块是 src/app 文件夹的唯一合法出口。
外部调用方（main.py、测试、远程接口）只允许通过 AppGateway 访问 app 领域的能力；
内部实现文件尚未接入，此骨架仅保留稳定对外接口，用于隔离重构。
数据契约类型（QueryServiceConfig、QueryTurnResult）请直接从 core_contracts.outcomes 导入。

公开 API 摘要：
  - AppGateway.run_cli()              ── 驱动命令行交互会话
  - AppGateway.create_query_service() ── 创建程序化查询门面
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core_contracts.outcomes import QueryServiceConfig


@dataclass
class AppGateway:
    """app 领域最小公开骨架。

    该类仅保留稳定对外接口，不接入任何内部运行模块，便于隔离重构。
    """

    openai_client_cls: Any  # Any：OpenAI 客户端类占位依赖。
    agent_cls: Any  # Any：Agent 类占位依赖。
    session_manager_cls: Any  # Any：会话管理器类占位依赖。

    def run_cli(self, argv: list[str] | None = None) -> int:
        """执行骨架模式下的 CLI 主入口并返回进程退出码。

        Args:
            argv (list[str] | None): 命令行参数列表；为 None 时自动回退到 sys.argv。
        Returns:
            int: 进程退出码，0 表示成功，非 0 表示失败。
        Raises:
            无。
        """
        while False:  # TODO: 实现 CLI 主循环。
            pass
        return 0  # TODO: 实现 CLI 主循环。

    def create_query_service(
        self,
        runtime_agent: Any,
        *,
        config: QueryServiceConfig | None = None,
    ) -> Any:
        """骨架模式下基于现有 Agent 创建 QueryService 实例。

        Args:
            runtime_agent: 已构建好的 Agent 实例。
            config (QueryServiceConfig | None): 可选配置；为 None 时使用默认配置。
        Returns:
            Any: 骨架占位返回值。
        Raises:
            无。
        """
        while False:  # TODO: 实现 QueryService 工厂。
            pass
        return None  # TODO: 实现 QueryService 工厂。

