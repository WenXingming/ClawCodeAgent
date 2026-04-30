"""client 模块门面网关。

该网关是 client 模块唯一公开入口，职责仅包括：
1. 实现 ModelClient 协议，暴露稳定的 complete/stream/complete_stream 接口。
2. 把外部消息列表转换为内部请求 DTO 并调度 OpenAIClient 执行。
3. 把内部异常统一翻译为共享模型网关异常。
"""

from __future__ import annotations

from typing import Iterator

from core_contracts.client_contracts import ClientExecutionError
from core_contracts.errors import ModelGatewayError
from core_contracts.messaging import OneTurnResponse, StreamEvent
from core_contracts.model import ModelClient, ModelConfig, StructuredOutputSpec
from core_contracts.primitives import JSONDict

from .openai_client import OpenAIClient


class ClientGateway(ModelClient):
    """实现 ModelClient 协议并将外部调用适配到内部 DTO 编排器的门面类。"""

    def __init__(
        self,
        model_config: ModelConfig,
        *,
        client: OpenAIClient,
    ) -> None:
        """接收工厂注入的内部客户端并保存依赖。
        Args:
            model_config (ModelConfig): 模型连接与运行配置（保留供子类扩展）。
            client (OpenAIClient): 由工厂注入的内部执行组件。
        Returns:
            None: 该方法只保存依赖，不创建任何实例。
        Raises:
            无。
        """
        self._model_config = model_config  # ModelConfig：模型运行配置依赖。
        self._client = client              # OpenAIClient：内部执行组件依赖。

    def complete(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> OneTurnResponse:
        """执行一次非流式模型调用。
        Args:
            messages (list[JSONDict]): 模型消息列表。
            tools (list[JSONDict] | None): 可选工具声明列表。
            output_schema (StructuredOutputSpec | None): 可选结构化输出契约。
        Returns:
            OneTurnResponse: 单轮模型响应。
        Raises:
            ModelGatewayError: 当执行失败时抛出。
        """
        try:
            return self._client.complete(messages, tools, output_schema=output_schema)
        except ClientExecutionError as exc:
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
            messages (list[JSONDict]): 模型消息列表。
            tools (list[JSONDict] | None): 可选工具声明列表。
            output_schema (StructuredOutputSpec | None): 可选结构化输出契约。
        Returns:
            Iterator[StreamEvent]: 流事件序列。
        Raises:
            ModelGatewayError: 当执行失败时抛出。
        """
        try:
            for event in self._client.stream(messages, tools, output_schema=output_schema):
                yield event
        except ClientExecutionError as exc:
            raise self._translate_error(exc) from exc

    def complete_stream(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: StructuredOutputSpec | None = None,
    ) -> OneTurnResponse:
        """执行流式调用并返回聚合结果。
        Args:
            messages (list[JSONDict]): 模型消息列表。
            tools (list[JSONDict] | None): 可选工具声明列表。
            output_schema (StructuredOutputSpec | None): 可选结构化输出契约。
        Returns:
            OneTurnResponse: 聚合后的单轮响应。
        Raises:
            ModelGatewayError: 当执行失败时抛出。
        """
        try:
            return self._client.complete_stream(messages, tools, output_schema=output_schema)
        except ClientExecutionError as exc:
            raise self._translate_error(exc) from exc

    def _translate_error(self, exc: ClientExecutionError) -> ModelGatewayError:
        """把内部客户端异常翻译为共享模型网关异常。
        Args:
            exc (ClientExecutionError): 内部异常对象。
        Returns:
            ModelGatewayError: 统一模型网关异常对象。
        Raises:
            无。
        """
        return ModelGatewayError(str(exc))
