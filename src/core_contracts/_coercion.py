"""核心契约包内各模块共用的安全类型转换工具。

本模块所有函数均为私有（以 _ 开头），仅供 core_contracts 内部使用，
不通过 __init__.py 对外暴露。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _first_present(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """按顺序返回第一个存在且非 None 的字段值。
    Args:
        data (JSONDict): 源字典。
        *keys (str): 候选字段名序列。
        default (Any): 全部缺失或为 None 时返回的默认值。
    Returns:
        Any: 首个命中值或默认值。
    """
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_int(value: Any, default: int = 0) -> int:
    """安全转换为 int。
    Args:
        value (Any): 待转换值。
        default (int): 转换失败时的默认值。
    Returns:
        int: 转换结果。
    """
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> int | None:
    """安全转换为 int 或 None。
    Args:
        value (Any): 待转换值。
    Returns:
        int | None: 转换结果；None 或转换失败时返回 None。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    """安全转换为 float。
    Args:
        value (Any): 待转换值。
        default (float): 转换失败时的默认值。
    Returns:
        float: 转换结果。
    """
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: Any) -> float | None:
    """安全转换为 float 或 None。
    Args:
        value (Any): 待转换值。
    Returns:
        float | None: 转换结果；None 或转换失败时返回 None。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any, default: bool = False) -> bool:
    """安全转换为 bool，支持常见字符串真值。
    Args:
        value (Any): 待转换值。
        default (bool): 转换失败时的默认值。
    Returns:
        bool: 转换结果。
    """
    if isinstance(value, (bool, int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', '1', 'yes', 'on'}:
            return True
        if lowered in {'false', '0', 'no', 'off', ''}:
            return False
        return default
    return default


def _as_str(value: Any, default: str = '') -> str:
    """安全转换为 str。
    Args:
        value (Any): 待转换值。
        default (str): value 为 None 时的默认值。
    Returns:
        str: 转换结果。
    """
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_optional_str(value: Any) -> str | None:
    """安全转换为 str 或 None。
    Args:
        value (Any): 待转换值。
    Returns:
        str | None: 转换结果；None 时返回 None。
    """
    if value is None:
        return None
    return _as_str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    """安全转换为 dict。
    Args:
        value (Any): 待转换值。
    Returns:
        dict[str, Any]: 输入为 dict 时的浅拷贝，否则返回空字典。
    """
    if isinstance(value, dict):
        return dict(value)
    return {}


def _path_or_default(value: Any, default: Path) -> Path:
    """安全转换为已解析的 Path。
    Args:
        value (Any): 待转换值。
        default (Path): 转换失败时的默认路径。
    Returns:
        Path: 解析后的绝对路径。
    """
    if isinstance(value, Path):
        return value.resolve()
    if isinstance(value, str):
        return Path(value).resolve()
    return default
