"""client 内部 OpenAIClient 编排器测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from core_contracts.client_contracts import ClientRequest
from core_contracts.messaging import OneTurnResponse, StreamEvent, ToolCall
from core_contracts.model import ModelConfig
from core_contracts.primitives import TokenUsage
from client.openai_client import OpenAIClient


@pytest.fixture
def model_config() -> ModelConfig:
	"""构造稳定的模型配置夹具。
	Args:
		无。
	Returns:
		ModelConfig: 固定模型配置对象。
	Raises:
		无。
	"""
	return ModelConfig(
		model='demo-model',
		base_url='http://127.0.0.1:8000/v1',
		api_key='test-token',
		temperature=0.1,
		timeout_seconds=15.0,
	)


def test_complete_uses_payload_builder_transport_and_completion_parser(model_config: ModelConfig) -> None:
	"""验证非流式主流程会串联构造器、传输器与解析器。
	Args:
		model_config (ModelConfig): 模型配置夹具。
	Returns:
		None.
	Raises:
		无。
	"""
	payload_builder = Mock()
	transport = Mock()
	completion_parser = Mock()
	response = OneTurnResponse(content='ok', tool_calls=tuple(), finish_reason='stop', usage=TokenUsage())
	payload_builder.build.return_value = {'payload': 'value'}
	transport.post_json.return_value = {'choices': []}
	completion_parser.parse.return_value = response
	client = OpenAIClient(
		payload_builder=payload_builder,
		transport=transport,
		completion_parser=completion_parser,
		sse_reader=Mock(),
		stream_parser=Mock(),
		stream_aggregator=Mock(),
	)

	result = client.complete(messages=[{'role': 'user', 'content': 'hi'}], tools=[])

	assert result == response
	invoke_request = payload_builder.build.call_args.args[0]
	assert isinstance(invoke_request, ClientRequest)
	payload_builder.build.assert_called_once()
	transport.post_json.assert_called_once_with({'payload': 'value'})
	completion_parser.parse.assert_called_once_with({'choices': []})


def test_stream_emits_message_start_and_parsed_events(model_config: ModelConfig) -> None:
	"""验证流式主流程会先发 message_start 再转发解析结果。
	Args:
		model_config (ModelConfig): 模型配置夹具。
	Returns:
		None.
	Raises:
		无。
	"""
	payload_builder = Mock()
	transport = Mock()
	sse_reader = Mock()
	stream_parser = Mock()
	response_context = MagicMock()
	response = response_context.__enter__.return_value
	parsed_event = StreamEvent(type='content_delta', delta='hello')
	payload_builder.build.return_value = {'payload': 'stream'}
	transport.open_event_stream.return_value = response_context
	sse_reader.iter_payloads.return_value = iter([{'chunk': 1}])
	stream_parser.parse_payload.return_value = iter([parsed_event])
	client = OpenAIClient(
		payload_builder=payload_builder,
		transport=transport,
		completion_parser=Mock(),
		sse_reader=sse_reader,
		stream_parser=stream_parser,
		stream_aggregator=Mock(),
	)

	events = list(client.stream(messages=[{'role': 'user', 'content': 'hi'}], tools=[]))

	assert [event.type for event in events] == ['message_start', 'content_delta']
	transport.open_event_stream.assert_called_once_with({'payload': 'stream'})
	sse_reader.iter_payloads.assert_called_once_with(response)
	stream_parser.parse_payload.assert_called_once_with({'chunk': 1})


def test_complete_stream_uses_aggregator(model_config: ModelConfig) -> None:
	"""验证流式聚合主流程会调用聚合器生成最终结果。
	Args:
		model_config (ModelConfig): 模型配置夹具。
	Returns:
		None.
	Raises:
		无。
	"""
	payload_builder = Mock()
	transport = Mock()
	sse_reader = Mock()
	stream_parser = Mock()
	stream_aggregator = Mock()
	response_context = Mock()
	aggregated = OneTurnResponse(
		content='hello',
		tool_calls=(ToolCall(id='call_1', name='read_file', arguments={'path': 'README.md'}),),
		finish_reason='tool_calls',
		usage=TokenUsage(),
	)
	payload_builder.build.return_value = {'payload': 'stream'}
	transport.open_event_stream.return_value = response_context
	sse_reader.iter_payloads.return_value = iter([{'chunk': 1}])
	stream_parser.parse_payload.return_value = iter([StreamEvent(type='message_stop', finish_reason='stop')])
	stream_aggregator.aggregate.return_value = aggregated
	client = OpenAIClient(
		payload_builder=payload_builder,
		transport=transport,
		completion_parser=Mock(),
		sse_reader=sse_reader,
		stream_parser=stream_parser,
		stream_aggregator=stream_aggregator,
	)

	result = client.complete_stream(messages=[{'role': 'user', 'content': 'hi'}], tools=[])

	assert result == aggregated
	stream_aggregator.aggregate.assert_called_once()
