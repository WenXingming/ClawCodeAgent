"""OpenAI-compatible 客户端包入口。"""

from urllib import request

from .client import (
    OpenAIClient,
    OpenAIClientError,
    OpenAIConnectionError,
    OpenAIResponseError,
    OpenAITimeoutError,
)

__all__ = [
    'OpenAIClient',
    'OpenAIClientError',
    'OpenAIConnectionError',
    'OpenAIResponseError',
    'OpenAITimeoutError',
    'request',
]