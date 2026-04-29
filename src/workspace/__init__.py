"""workspace 领域公共导出。

本包仅对外暴露 `WorkspaceGateway`。
`workspace` 目录内其余模块均视为内部实现细节，不提供跨域导入稳定性承诺。
"""

from workspace.workspace_gateway import WorkspaceGateway

__all__ = ['WorkspaceGateway']