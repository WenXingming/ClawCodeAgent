"""app 领域公开入口。

外部模块只允许通过 AppGateway 访问 app 能力。
数据契约类型（QueryServiceConfig、QueryTurnResult）请直接从 core_contracts.outcomes 导入。
"""

from __future__ import annotations

from app.app_gateway import AppGateway

__all__ = ['AppGateway']
