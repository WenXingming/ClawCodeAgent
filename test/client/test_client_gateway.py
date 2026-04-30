"""Pytest coverage for client facade gateway.

Focus areas:
- ModelClient 协议方法（complete/stream/complete_stream）行为验证
- error translation boundary guarantees
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from core_contracts.client_contracts import ClientExecutionError
from core_contracts.errors import ModelGatewayError
from core_contracts.messaging import OneTurnResponse, StreamEvent
from core_contracts.model import ModelConfig
from core_contracts.primitives import TokenUsage
from client.client_gateway import ClientGateway


@pytest.fixture
def model_config() -> ModelConfig:
    """Build deterministic model configuration for gateway tests.
    Args:
        None.
    Returns:
        ModelConfig: Fixed test model configuration.
    Raises:
        None.
    """
    return ModelConfig(model='demo-model', base_url='http://127.0.0.1:8000/v1', api_key='k')


def test_complete_routes_to_internal_complete(model_config: ModelConfig) -> None:
    """验证 complete() 会调用内部客户端同名接口并返回响应内容。
    Args:
        model_config (ModelConfig): Model config fixture.
    Returns:
        None.
    Raises:
        None.
    """
    internal_client = Mock()
    internal_client.complete.return_value = OneTurnResponse(
        content='hello', tool_calls=tuple(), finish_reason='stop', usage=TokenUsage()
    )
    gateway = ClientGateway(model_config, client=internal_client)

    response = gateway.complete(messages=[{'role': 'user', 'content': 'hi'}], tools=[])

    assert response.content == 'hello'
    internal_client.complete.assert_called_once()


def test_complete_adapts_legacy_signature(model_config: ModelConfig) -> None:
    """Validate complete() forwards legacy signature to internal complete().
    Args:
        model_config (ModelConfig): Model config fixture.
    Returns:
        None.
    Raises:
        None.
    """
    internal_client = Mock()
    internal_client.complete.return_value = OneTurnResponse(
        content='adapter-ok', tool_calls=tuple(), finish_reason='stop', usage=TokenUsage()
    )
    gateway = ClientGateway(model_config, client=internal_client)

    response = gateway.complete(messages=[{'role': 'user', 'content': 'hi'}], tools=[])

    assert response.content == 'adapter-ok'
    internal_client.complete.assert_called_once()


def test_stream_adapts_legacy_signature(model_config: ModelConfig) -> None:
    """Validate stream() adapter yields StreamEvent values.
    Args:
        model_config (ModelConfig): Model config fixture.
    Returns:
        None.
    Raises:
        None.
    """
    internal_client = Mock()
    internal_client.stream.return_value = iter(
        [
            StreamEvent(type='message_start'),
            StreamEvent(type='content_delta', delta='hi'),
            StreamEvent(type='message_stop', finish_reason='stop'),
        ]
    )
    gateway = ClientGateway(model_config, client=internal_client)

    events = list(gateway.stream(messages=[{'role': 'user', 'content': 'hi'}], tools=[]))

    assert [event.type for event in events] == ['message_start', 'content_delta', 'message_stop']


def test_complete_translates_client_execution_error(model_config: ModelConfig) -> None:
    """Validate ClientExecutionError translation into ModelGatewayError.
    Args:
        model_config (ModelConfig): Model config fixture.
    Returns:
        None.
    Raises:
        None.
    """
    internal_client = Mock()
    internal_client.complete.side_effect = ClientExecutionError('bad response')
    gateway = ClientGateway(model_config, client=internal_client)

    with pytest.raises(ModelGatewayError) as captured:
        gateway.complete(messages=[{'role': 'user', 'content': 'hi'}], tools=[])

    assert 'bad response' in str(captured.value)


def test_complete_translates_generic_client_execution_error(model_config: ModelConfig) -> None:
    """验证通用内部执行异常会被翻译为 ModelGatewayError。
    Args:
        model_config (ModelConfig): 模型配置夹具。
    Returns:
        None.
    Raises:
        无。
    """
    internal_client = Mock()
    internal_client.complete.side_effect = ClientExecutionError('generic failure')
    gateway = ClientGateway(model_config, client=internal_client)

    with pytest.raises(ModelGatewayError):
        gateway.complete(messages=[{'role': 'user', 'content': 'hi'}], tools=[])
