"""agent 领域公开入口。

该包只暴露网关类型 `AgentGateway`。
外部模块不应直接导入本目录下的内部实现文件。
"""

from __future__ import annotations

__all__ = ['AgentGateway']


def __getattr__(name: str):
	"""按需导出 AgentGateway，避免包初始化时的循环导入。
	Args:
		name (str): 请求访问的属性名。
	Returns:
		type: 当 name 为 AgentGateway 时返回网关类。
	Raises:
		AttributeError: 当 name 不是公开导出符号时抛出。
	"""
	if name == 'AgentGateway':
		from agent.agent_gateway import AgentGateway

		return AgentGateway
	raise AttributeError(f'module {__name__!r} has no attribute {name!r}')