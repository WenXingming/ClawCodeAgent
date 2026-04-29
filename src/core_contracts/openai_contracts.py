"""openai_client 域跨模块共享契约。"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from core_contracts.gateway_errors import GatewayRuntimeError, GatewayTransportError, GatewayValidationError
from core_contracts.model import ModelConfig, StructuredOutputSpec
from core_contracts.protocol import JSONDict, OneTurnResponse, StreamEvent


class ModelGatewayError(GatewayRuntimeError):
    """模型网关基础异常。"""


class ModelConnectionError(GatewayTransportError):
    """模型后端连接失败。"""


class ModelTimeoutError(GatewayTransportError):
    """模型后端超时。"""


class ModelResponseError(GatewayValidationError):
    """模型后端响应结构异常。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail if detail is not None else message


@runtime_checkable
class ModelClient(Protocol):
    """跨模块使用的模型调用最小接口。"""

    model_config: ModelConfig

    def complete(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> OneTurnResponse:
        """执行一次非流式模型调用。"""

    def stream(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> Iterator[StreamEvent]:
        """执行一次流式模型调用。"""

    def complete_stream(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> OneTurnResponse:
        """执行一次流式聚合模型调用。"""
