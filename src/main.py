"""提供 CLI 进程的最顶层入口。

本模块只负责核心依赖注入，把命令行参数转发给 AppGateway.run_cli()。
控制面实现下沉到 app 层，此处仅保留最薄的入口与可 patch 的依赖注入点。
"""

from __future__ import annotations

from app.app_gateway import AppGateway
from agent import AgentGateway as Agent
from openai_client import OpenAIClientGateway
from session import SessionGateway


def main(argv: list[str] | None = None) -> int:
    """执行主 CLI 入口并返回进程退出码。

    通过 AppGateway.run_cli() 把依赖注入点与控制面实现完全解耦。
    测试可 patch OpenAIClientGateway、Agent 或 SessionGateway 来替换实际实现。

    Args:
        argv (list[str] | None): 命令行参数列表；None 时由 argparse 回退到 sys.argv。
    Returns:
        int: 进程退出码，0 表示成功，非 0 表示失败。
    Raises:
        无（内部异常已在 AppGateway 层捕获）。
    """
    return AppGateway.run_cli(
        argv,
        openai_client_cls=OpenAIClientGateway,
        agent_cls=Agent,
        session_manager_cls=SessionGateway,
    )


if __name__ == '__main__':
    raise SystemExit(main())
