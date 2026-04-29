"""openai_client 领域统一网关。

该模块是 openai_client 文件夹唯一公开入口实现，负责：
1. 接收 core_contracts 定义的模型请求契约；
2. 调用内部 OpenAI-compatible 客户端完成请求；
3. 把内部异常统一翻译成 core_contracts.openai_contracts 异常族。
"""

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
    """封装 openai_client 内部实现并翻译为通用契约异常。

    核心工作流：
    1. 构造时绑定 ModelConfig 并实例化内部 OpenAIClient；
    2. public API 直接转发 complete/stream/complete_stream；
    3. 捕获内部异常并经 _translate_error 转换成模型网关契约异常。
    """

    def __init__(self, model_config: ModelConfig) -> None:
        """初始化 OpenAIClientGateway。
        Args:
            model_config (ModelConfig): 模型配置契约对象。
        Returns:
            None
        Raises:
            无。
        """
        self._client = OpenAIClient(model_config)  # OpenAIClient：内部 HTTP 客户端实现，仅在网关内可见。

    @property
    def model_config(self) -> ModelConfig:
        """返回当前网关绑定的模型配置。
        Args:
            无
        Returns:
            ModelConfig: 当前模型配置。
        Raises:
            无。
        """
        return self._client.model_config

    def complete(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> OneTurnResponse:
        """执行一次非流式模型调用。
        Args:
            messages (list[JSONDict]): 模型对话消息列表。
            tools (list[JSONDict] | None): 可选工具定义。
            output_schema (StructuredOutputSpec | None): 可选结构化输出约束。
        Returns:
            OneTurnResponse: 单轮完整响应。
        Raises:
            ModelGatewayError: 当内部调用失败时抛出转换后的网关异常。
        """
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
        """执行一次流式模型调用。
        Args:
            messages (list[JSONDict]): 模型对话消息列表。
            tools (list[JSONDict] | None): 可选工具定义。
            output_schema (StructuredOutputSpec | None): 可选结构化输出约束。
        Returns:
            Iterator[StreamEvent]: 流式事件序列。
        Raises:
            ModelGatewayError: 当内部调用失败时抛出转换后的网关异常。
        """
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
        """执行一次流式聚合模型调用。
        Args:
            messages (list[JSONDict]): 模型对话消息列表。
            tools (list[JSONDict] | None): 可选工具定义。
            output_schema (StructuredOutputSpec | None): 可选结构化输出约束。
        Returns:
            OneTurnResponse: 聚合后的单轮完整响应。
        Raises:
            ModelGatewayError: 当内部调用失败时抛出转换后的网关异常。
        """
        try:
            return self._client.complete_stream(messages=messages, tools=tools, output_schema=output_schema)
        except OpenAIClientError as exc:
            raise self._translate_error(exc) from exc

    def _translate_error(self, exc: OpenAIClientError) -> ModelGatewayError:
        """把 openai_client 私有异常翻译为 core_contracts 通用异常。
        Args:
            exc (OpenAIClientError): 内部客户端异常对象。
        Returns:
            ModelGatewayError: 映射后的统一模型网关异常。
        Raises:
            无。
        """
        if isinstance(exc, OpenAIResponseError):
            return ModelResponseError(str(exc), status_code=exc.status_code, detail=exc.detail)
        if isinstance(exc, OpenAITimeoutError):
            return ModelTimeoutError(str(exc))
        if isinstance(exc, OpenAIConnectionError):
            return ModelConnectionError(str(exc))
        return ModelGatewayError(str(exc))
