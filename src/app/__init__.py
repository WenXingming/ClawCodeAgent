"""app 领域公开入口。

外部模块只允许通过 AppGateway 访问 app 能力；
QueryService、QueryTurnResult、QueryServiceConfig 作为数据契约类型亦可从此处导入。
禁止直接导入 app.cli / app.chat_loop / app.runtime_builder / app.query_service。
"""

from __future__ import annotations

from app.app_gateway import AppGateway, QueryServiceConfig, QueryTurnResult

__all__ = ['AppGateway', 'QueryServiceConfig', 'QueryTurnResult']
