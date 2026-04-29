"""context 领域公共导出。"""

from __future__ import annotations

from typing import Any

__all__ = ['BudgetProjection', 'ContextGateway']

_EXPORTS: dict[str, str] = {
	'ContextGateway': 'context.context_gateway',
	'BudgetProjection': 'context.budget_projection',
}


def __getattr__(name: str) -> Any:
	"""延迟导出公共门面，避免包初始化时触发循环依赖。"""
	module_path = _EXPORTS.get(name)
	if module_path is None:
		raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

	import importlib
	module = importlib.import_module(module_path)
	return getattr(module, name)