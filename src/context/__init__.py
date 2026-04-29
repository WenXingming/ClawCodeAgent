"""context 领域公共导出。"""

from __future__ import annotations

from typing import Any

__all__ = ['ContextManager']


def __getattr__(name: str) -> Any:
	"""延迟导出公共门面，避免包初始化时触发循环依赖。"""
	if name != 'ContextManager':
		raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

	from context.context_manager import ContextManager

	return ContextManager