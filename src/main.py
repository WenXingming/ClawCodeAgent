"""提供 CLI 进程的最顶层入口。

本模块只负责组织控制面、运行时、客户端与会话存储等核心依赖的注入，并把命令行参数转发给 `interaction.command_line_interaction`。控制面实现已经下沉到 interaction 层，这里仅保留最薄的入口与可 patch 的依赖注入点。
"""

from __future__ import annotations

from agent import Agent
from interaction.command_line_interaction import CLI
from openai_client.openai_client import OpenAIClient
from session.session_store import AgentSessionStore


def main(argv: list[str] | None = None) -> int:
    """执行主 CLI 入口并返回进程退出码。

    该函数根据传入的命令行参数创建 `CLI` 实例，并注入 `OpenAIClient`、`Agent` 和 `AgentSessionStore` 作为默认依赖。测试可通过 patch 这些注入点来替换实际实现。

    Args:
        argv (list[str] | None): 命令行参数列表；为 None 时由 CLI 自行回退到默认参数来源。
    Returns:
        int: 进程退出码，0 表示成功，非 0 表示失败。
    """
    cli = CLI(
        openai_client_cls=OpenAIClient,
        agent_cls=Agent,
        session_store_cls=AgentSessionStore,
    )
    return cli.main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
