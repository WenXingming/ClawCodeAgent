"""CLI 进程入口。

控制面实现已下沉到 control_plane.cli；
本文件仅保留顶层入口与可 patch 的依赖注入点。
"""

from __future__ import annotations

from control_plane.cli import main as _control_plane_main
from openai_client.openai_client import OpenAIClient
from runtime.agent_runtime import LocalCodingAgent
from session.session_store import load_agent_session


def main(argv: list[str] | None = None) -> int:
    return _control_plane_main(
        argv,
        openai_client_cls=OpenAIClient,
        agent_cls=LocalCodingAgent,
        load_session=load_agent_session,
    )


if __name__ == '__main__':
    raise SystemExit(main())
