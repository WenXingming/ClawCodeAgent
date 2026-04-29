"""网关与模型统一错误契约。

集中定义所有跨领域异常类型：
  - 网关层: GatewayError 及其语义化子类。
  - 模型层: ModelGatewayError 及其传输/超时/响应子类。
"""

from __future__ import annotations


class GatewayError(RuntimeError):
    """所有 gateway 层异常的统一基类。"""


class GatewayPermissionError(GatewayError):
    """表示当前调用被权限策略拒绝。"""


class GatewayTransportError(GatewayError):
    """表示网关到外部系统的传输或连接失败。"""


class GatewayValidationError(GatewayError):
    """表示调用参数或输入状态不合法。"""


class GatewayRuntimeError(GatewayError):
    """表示网关执行过程中发生的运行时错误。"""


class GatewayNotFoundError(GatewayError):
    """表示请求的实体或能力不存在。"""


class ModelGatewayError(GatewayRuntimeError):
    """模型网关基础异常，表示模型后端相关的运行时错误。"""


class ModelConnectionError(GatewayTransportError):
    """模型后端连接失败。"""


class ModelTimeoutError(GatewayTransportError):
    """模型后端超时。"""


class ModelResponseError(GatewayValidationError):
    """模型后端响应结构异常。

    Attributes:
        status_code (int | None): HTTP 状态码。
        detail (str): 错误详情字符串。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        """初始化响应错误对象。
        Args:
            message (str): 错误消息主体。
            status_code (int | None): HTTP 状态码。
            detail (str | None): 详细错误描述。
        """
        super().__init__(message)
        self.status_code = status_code  # int | None：HTTP 状态码。
        self.detail = detail if detail is not None else message  # str：详细错误描述。
