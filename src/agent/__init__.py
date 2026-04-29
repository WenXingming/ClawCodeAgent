"""agent 领域公开入口。"""

from __future__ import annotations

__all__ = ['Agent']


def __getattr__(name: str):
	"""按需导出 Agent，避免包初始化时的循环导入。"""
	if name == 'Agent':
		from agent.agent import Agent

		return Agent
	raise AttributeError(f'module {__name__!r} has no attribute {name!r}')