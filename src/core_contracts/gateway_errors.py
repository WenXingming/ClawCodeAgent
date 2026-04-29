"""跨领域网关统一异常契约。"""

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
