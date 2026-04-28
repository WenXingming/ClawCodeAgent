"""CLI 进程入口。

本文件是最顶层的 Python 包入口，负责：
- 组织所有控制面、运行时、客户端等组件的依赖注入
- 通过@patch点可擦写关键依赖（OpenAIClient、LocalCodingAgent、AgentSessionStore）
- 转发CLI命令到interaction.command_line_interaction的主逻辑

控制面实现已下沉到 interaction.command_line_interaction；
本文件仅保留顶层入口与可 patch 的依赖注入点。
"""

from __future__ import annotations

from interaction.command_line_interaction import CLI
from openai_client.openai_client import OpenAIClient
from orchestration.local_agent import LocalAgent
from session.session_store import AgentSessionStore


def main(argv: list[str] | None = None) -> int:
    """主CLI入口函数。
    
    以来自命令行的参数或test injected arguments构造Agent并执行，返回进程退出码。
    可通过@patch装饰器注入测试用的openai_client_cls/agent_cls/session_store_cls。
    Args:
        argv (list[str] | None): 命令行参数列表；None时使用sys.argv[1:]
    Returns:
        int: 进程退出码（0=成功，非0=失败）
    """
    cli = CLI(
        openai_client_cls=OpenAIClient,
        agent_cls=LocalAgent,
        session_store_cls=AgentSessionStore,
    )
    return cli.main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
