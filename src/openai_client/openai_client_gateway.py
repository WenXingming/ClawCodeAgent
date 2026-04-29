"""openai_client 域统一网关。"""

from __future__ import annotations

from typing import Iterator

from core_contracts.model import ModelConfig, StructuredOutputSpec
from core_contracts.openai_contracts import (
    ModelClient,
    ModelConnectionError,
    ModelGatewayError,
    ModelResponseError,
    ModelTimeoutError,
)
from core_contracts.protocol import JSONDict, OneTurnResponse, StreamEvent

from .openai_client import OpenAIClient, OpenAIClientError, OpenAIConnectionError, OpenAIResponseError, OpenAITimeoutError


class OpenAIClientGateway(ModelClient):
    """封装 openai_client 内部实现并翻译为通用契约异常。"""

    def __init__(self, client_or_model_config: OpenAIClient | ModelConfig) -> None:
        if isinstance(client_or_model_config, OpenAIClient):
            self._client = client_or_model_config
        else:
            self._client = OpenAIClient(client_or_model_config)

    @property
    def model_config(self) -> ModelConfig:
        """返回当前网关绑定的模型配置。"""
        return self._client.model_config

    def complete(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> OneTurnResponse:
        """执行一次非流式模型调用。"""
        try:
            return self._client.complete(messages=messages, tools=tools, output_schema=output_schema)
        except OpenAIClientError as exc:
            raise self._translate_error(exc) from exc

    def stream(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> Iterator[StreamEvent]:
        """执行一次流式模型调用。"""
        try:
            for event in self._client.stream(messages=messages, tools=tools, output_schema=output_schema):
                yield event
        except OpenAIClientError as exc:
            raise self._translate_error(exc) from exc

    def complete_stream(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> OneTurnResponse:
        """执行一次流式聚合模型调用。"""
        try:
            return self._client.complete_stream(messages=messages, tools=tools, output_schema=output_schema)
        except OpenAIClientError as exc:
            raise self._translate_error(exc) from exc

    def _translate_error(self, exc: OpenAIClientError) -> ModelGatewayError:
        """把 openai_client 私有异常翻译为 core_contracts 通用异常。"""
        if isinstance(exc, OpenAIResponseError):
            return ModelResponseError(str(exc), status_code=exc.status_code, detail=exc.detail)
        if isinstance(exc, OpenAITimeoutError):
            return ModelTimeoutError(str(exc))
        if isinstance(exc, OpenAIConnectionError):
            return ModelConnectionError(str(exc))
        return ModelGatewayError(str(exc))
