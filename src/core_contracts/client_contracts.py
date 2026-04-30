"""client 模块对外契约。

本文件定义 client 模块的请求/结果 DTO 与通用异常。
上层编排器与 client 模块交互时应优先使用这里的契约，
避免直接耦合内部实现细节。
"""

from __future__ import annotations

from dataclasses import dataclass

from .messaging import OneTurnResponse, StreamEvent
from .model import StructuredOutputSpec
from .primitives import JSONDict


class ClientContractError(RuntimeError):
    """client 模块契约错误基类。"""


class ClientExecutionError(ClientContractError):
    """client 模块执行失败的统一异常。"""


@dataclass(frozen=True)
class ClientRequest:
    """client 模块统一请求 DTO。"""

    messages: list[JSONDict]
    tools: list[JSONDict] | None = None
    output_schema: StructuredOutputSpec | None = None

    def __post_init__(self) -> None:
        """在 DTO 边界执行轻量校验与标准化。
        Args:
            无。
        Returns:
            None: 该方法会原位写回冻结数据类字段。
        Raises:
            ClientContractError: 当 messages 或 tools 结构非法时抛出。
        """
        object.__setattr__(self, 'messages', self._normalize_messages(self.messages))
        object.__setattr__(self, 'tools', self._normalize_tools(self.tools))

    @staticmethod
    def _normalize_messages(messages: object) -> list[JSONDict]:
        """校验并复制消息列表。
        Args:
            messages (object): 原始消息输入。
        Returns:
            list[JSONDict]: 标准化后的消息列表。
        Raises:
            ClientContractError: 当 messages 不是字典列表时抛出。
        """
        if not isinstance(messages, list):
            raise ClientContractError('ClientRequest.messages must be a list')
        normalized_messages: list[JSONDict] = []
        for item in messages:
            if not isinstance(item, dict):
                raise ClientContractError('ClientRequest.messages must contain dictionaries only')
            normalized_messages.append(dict(item))
        return normalized_messages

    @staticmethod
    def _normalize_tools(tools: object) -> list[JSONDict] | None:
        """校验并复制工具定义列表。
        Args:
            tools (object): 原始工具输入。
        Returns:
            list[JSONDict] | None: 标准化后的工具定义列表。
        Raises:
            ClientContractError: 当 tools 不是字典列表时抛出。
        """
        if tools is None:
            return None
        if not isinstance(tools, list):
            raise ClientContractError('ClientRequest.tools must be a list or None')
        normalized_tools: list[JSONDict] = []
        for item in tools:
            if not isinstance(item, dict):
                raise ClientContractError('ClientRequest.tools must contain dictionaries only')
            normalized_tools.append(dict(item))
        return normalized_tools

    @classmethod
    def from_legacy(
        cls,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        output_schema: StructuredOutputSpec | None = None,
    ) -> 'ClientRequest':
        """从旧接口参数构造 ClientRequest。
        Args:
            messages (list[JSONDict]): 模型消息列表。
            tools (list[JSONDict] | None): 工具定义列表。
            output_schema (StructuredOutputSpec | None): 结构化输出约束。
        Returns:
            ClientRequest: 统一请求 DTO。
        Raises:
            ClientContractError: 当输入结构非法时抛出。
        """
        return cls(messages=messages, tools=tools, output_schema=output_schema)
