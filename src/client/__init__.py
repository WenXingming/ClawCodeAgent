"""client 模块公开入口。

本模块是 client 领域的唯一对外门面，核心职责：
1. 通过 ClientGateway 统一暴露模型调用能力。
2. 通过 create_client_gateway 工厂完成全部内部依赖装配。
3. 对外屏蔽 client 内部组件细节，避免上层直接耦合内部实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .client_gateway import ClientGateway

if TYPE_CHECKING:
	from core_contracts.model import ModelConfig
	from .openai_client import OpenAIClient


def create_client_gateway(
	*,
	model_config: 'ModelConfig',
	client: 'OpenAIClient | None' = None,
) -> ClientGateway:
	"""构造全部内部依赖并返回 ClientGateway。

	模块内部的 SRP 组件（PayloadBuilder、HttpTransport 等）在此统一创建；
	模块外部依赖（model_config）通过参数注入；
	可选的 client 参数供测试替换整个内部客户端。

	Args:
		model_config (ModelConfig): 模型配置契约对象（外部注入）。
		client (OpenAIClient | None): 可选替换的内部客户端；传入时跳过内部组件装配。
	Returns:
		ClientGateway: 已装配完成的 client 门面实例。
	Raises:
		无。
	"""
	if client is None:
		from .openai_client import (
			CompletionParser,
			EndpointResolver,
			HttpRequestFactory,
			HttpTransport,
			OpenAIClient,
			PayloadBuilder,
			SSEReader,
			StreamEventParser,
			StreamResultAggregator,
		)

		endpoint_resolver = EndpointResolver(model_config)
		request_factory = HttpRequestFactory(endpoint_resolver, model_config)
		completion_parser = CompletionParser()
		client = OpenAIClient(
			payload_builder=PayloadBuilder(model_config),
			transport=HttpTransport(request_factory=request_factory, model_config=model_config),
			completion_parser=completion_parser,
			sse_reader=SSEReader(),
			stream_parser=StreamEventParser(completion_parser),
			stream_aggregator=StreamResultAggregator(),
		)
	return ClientGateway(model_config=model_config, client=client)


__all__ = [
	'ClientGateway',
	'create_client_gateway',
]

