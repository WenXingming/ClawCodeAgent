"""interaction 包公开入口。

对外只暴露 InteractionGateway，禁止依赖子模块路径。
"""

from .interaction_gateway import InteractionGateway

__all__ = ['InteractionGateway']
