"""app 领域公开入口。"""

from __future__ import annotations

__all__ = ['AppCLI', 'QueryService']


def __getattr__(name: str):
	"""按需导出 app 门面，避免不必要的导入级联。"""
	if name == 'AppCLI':
		from app.cli import AppCLI

		return AppCLI
	if name == 'QueryService':
		from app.query_service import QueryService

		return QueryService
	raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
