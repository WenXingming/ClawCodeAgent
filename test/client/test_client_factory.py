"""client 模块工厂函数测试。"""

from __future__ import annotations

from unittest.mock import Mock

from core_contracts.model import ModelConfig
from client import ClientGateway, create_client_gateway


def test_create_client_gateway_returns_gateway_instance() -> None:
    """验证 create_client_gateway 返回 ClientGateway 实例。
    Args:
        无。
    Returns:
        None: 通过断言验证工厂输出类型。
    Raises:
        无。
    """
    gateway = create_client_gateway(
        model_config=ModelConfig(model='demo-model', base_url='http://127.0.0.1:8000/v1', api_key='k')
    )
    assert isinstance(gateway, ClientGateway)


def test_create_client_gateway_prefers_injected_client() -> None:
    """验证 create_client_gateway 优先使用注入的内部 client。
    Args:
        无。
    Returns:
        None: 通过断言验证依赖注入优先级。
    Raises:
        无。
    """
    injected_client = Mock()
    gateway = create_client_gateway(
        model_config=ModelConfig(model='demo-model', base_url='http://127.0.0.1:8000/v1', api_key='k'),
        client=injected_client,
    )
    assert gateway._client is injected_client


def test_create_client_gateway_builds_default_internal_client() -> None:
    """验证 create_client_gateway 会装配默认内部客户端。
    Args:
        无。
    Returns:
        None: 通过断言验证默认装配结果。
    Raises:
        无。
    """
    gateway = create_client_gateway(
        model_config=ModelConfig(model='demo-model', base_url='http://127.0.0.1:8000/v1', api_key='k')
    )

    assert isinstance(gateway, ClientGateway)
    assert gateway._client.__class__.__name__ == 'OpenAIClient'
