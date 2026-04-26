"""核心契约层内部共用的解析辅助函数。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _first_present(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """按顺序返回第一个存在且非 None 的字段值。
    
    Args:
        data (dict): 待查询字典
        *keys (str): 字段名候选列表，按顺序检查
        default (Any): 所有字段都不存在时返回的默认值
        
    Returns:
        Any: 第一个存在的非 None 字段值，或默认值
    """
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_int(value: Any, default: int = 0) -> int:
    """安全地将值转换为 int，遇到异常时返回默认值。
    
    Args:
        value (Any): 待转换值；bool 与 None 直接返回默认值
        default (int): 转换失败时的默认值
        
    Returns:
        int: 转换后的整数或默认值
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
    """将值转换为 int 或 None。
    
    Args:
        value (Any): 待转换值；bool 与 None 直接返回 None
        
    Returns:
        int | None: 转换后的整数或 None
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    """安全地将值转换为 float，遇到异常时返回默认值。
    
    Args:
        value (Any): 待转换值；bool 与 None 直接返回默认值
        default (float): 转换失败时的默认值
        
    Returns:
        float: 转换后的浮点数或默认值
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
    """将值转换为 float 或 None。
    
    Args:
        value (Any): 待转换值；bool 与 None 直接返回 None
        
    Returns:
        float | None: 转换后的浮点数或 None
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any, default: bool = False) -> bool:
    """将值转换为 bool，支持常见字符串表示。
    
    Args:
        value (Any): 待转换值；支持 'true'/'false'/'1'/'0'/'yes'/'no'/'on'/'off'（不区分大小写）
        default (bool): 无法识别时的默认值
        
    Returns:
        bool: 转换后的布尔值或默认值
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', '1', 'yes', 'on'}:
            return True
        if lowered in {'false', '0', 'no', 'off'}:
            return False
    return default


def _as_str(value: Any, default: str = '') -> str:
    """将值转换为 str，遇到异常时返回默认值。
    
    Args:
        value (Any): 待转换值；None 直接返回默认值
        default (str): 转换失败或值为 None 时的默认值
        
    Returns:
        str: 转换后的字符串或默认值
    """
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_optional_str(value: Any) -> str | None:
    """将值转换为 str 或 None。
    
    Args:
        value (Any): 待转换值；None 直接返回 None
        
    Returns:
        str | None: 转换后的字符串或 None
    """
    if value is None:
        return None
    return _as_str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    """将值转换为 dict，遇到异常时返回空 dict。
    
    Args:
        value (Any): 待转换值；非 dict 类型或异常时返回空 dict
        
    Returns:
        dict: 转换后的字典或空 dict
    """
    if isinstance(value, dict):
        return dict(value)
    return {}


def _path_or_default(value: Any, default: Path) -> Path:
    """将值转换为 Path 并解析为绝对路径。
    
    Args:
        value (Any): 待转换值；字符串或 Path 对象均可
        default (Path): 值为空时的默认路径
        
    Returns:
        Path: 解析后的绝对路径
    """
    text = _as_str(value, '')
    if not text:
        return default.resolve()
    return Path(text).resolve()