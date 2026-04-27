"""Shell 命令安全策略。

该模块负责在真正执行 shell 之前做静态风险检查，并把权限开关与命令特征
组合为统一的允许/拒绝结果，供 local_tools 中的 bash 工具复用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SecurityBehavior(Enum):
    """表示一次 shell 安全检查的高层结论。"""

    ALLOW = 'allow'  # str: 明确允许执行。
    DENY = 'deny'  # str: 明确拒绝执行。
    PASSTHROUGH = 'passthrough'  # str: 需要结合上层权限继续判断。


@dataclass(frozen=True)
class SecurityResult:
    """表示一次 shell 安全检查的结构化结果。"""

    behavior: SecurityBehavior  # SecurityBehavior: 当前命令的安全判定结果。
    message: str  # str: 面向上层展示的解释信息。


_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

_SUBSTITUTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'\$\('), '$() command substitution'),
    (re.compile(r'`'), '`...` command substitution'),
    (re.compile(r'<\('), '<() process substitution'),
    (re.compile(r'>\('), '>() process substitution'),
    (re.compile(r'\$\{'), '${} parameter expansion'),
]

_DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'(^|[;&|]\s*)rm\s+-[a-zA-Z]*[rR][a-zA-Z]*[fF]?'), 'Potential data deletion via rm'),
    (re.compile(r'(^|[;&|]\s*)del\s+'), 'Potential data deletion via del'),
    (re.compile(r'(^|[;&|]\s*)rmdir\s+'), 'Potential directory deletion via rmdir'),
    (re.compile(r'\bgit\s+reset\s+--hard\b'), 'Potential data loss via git reset --hard'),
    (re.compile(r'\bgit\s+clean\b[^\n;&|]*-[a-zA-Z]*f'), 'Potential data loss via git clean -f'),
    (re.compile(r'\bmkfs(\.[a-z0-9]+)?\b'), 'Potential filesystem destruction via mkfs'),
    (re.compile(r'\bdd\s+[^\n]*\bof='), 'Potential destructive write via dd of='),
    (re.compile(r'(^|[;&|]\s*)shutdown\b'), 'Potential shutdown command'),
    (re.compile(r'(^|[;&|]\s*)reboot\b'), 'Potential reboot command'),
    (re.compile(r'(^|[;&|]\s*):\s*>'), 'Potential file truncation with : > file'),
]

_READ_ONLY_COMMANDS = frozenset(
    {
        'cat',
        'type',
        'head',
        'tail',
        'more',
        'less',
        'grep',
        'rg',
        'findstr',
        'ls',
        'dir',
        'tree',
        'pwd',
        'cd',
        'echo',
        'printf',
        'whoami',
        'hostname',
        'date',
        'time',
        'git',
    }
)


def split_command(command: str) -> list[str]:
    """按链式操作符拆分命令，同时保留引号内文本。

    Args:
        command (str): 原始 shell 命令字符串。
    Returns:
        list[str]: 拆分后的命令片段列表。
    """
    if not command:
        return []

    parts: list[str] = []
    start = 0
    idx = 0
    in_single = False
    in_double = False
    escaped = False

    while idx < len(command):
        ch = command[idx]

        if escaped:
            escaped = False
            idx += 1
            continue

        if ch == '\\' and not in_single:
            escaped = True
            idx += 1
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            idx += 1
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            idx += 1
            continue

        if not in_single and not in_double:
            two = command[idx : idx + 2]
            if two in {'&&', '||'}:
                segment = command[start:idx].strip()
                if segment:
                    parts.append(segment)
                idx += 2
                start = idx
                continue

            if ch in {';', '|'}:
                segment = command[start:idx].strip()
                if segment:
                    parts.append(segment)
                idx += 1
                start = idx
                continue

        idx += 1

    tail = command[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def check_shell_security(
    command: str,
    *,
    allow_shell: bool,
    allow_destructive: bool,
) -> tuple[bool, str]:
    """结合权限开关与命令风险判断是否允许执行。

    Args:
        command (str): 待执行的 shell 命令。
        allow_shell (bool): 是否允许使用 shell 能力。
        allow_destructive (bool): 是否允许执行破坏性命令。
    Returns:
        tuple[bool, str]: 是否允许执行，以及拒绝原因。
    """
    if not allow_shell:
        return False, 'Shell commands are disabled: allow_shell_commands=false'

    result = bash_command_is_safe(command)
    if result.behavior == SecurityBehavior.DENY and not allow_destructive:
        return False, result.message

    return True, ''


def bash_command_is_safe(command: str) -> SecurityResult:
    """仅基于命令文本判断 shell 命令是否安全。

    Args:
        command (str): 待检查的 shell 命令。
    Returns:
        SecurityResult: 安全结论与解释信息。
    """
    stripped = command.strip()
    if not stripped:
        return SecurityResult(SecurityBehavior.ALLOW, 'Empty command is safe')

    if _contains_control_characters(stripped):
        return SecurityResult(SecurityBehavior.DENY, 'Command contains control characters')

    substitution = _match_command_substitution(stripped)
    if substitution is not None:
        return SecurityResult(SecurityBehavior.DENY, f'Command contains {substitution}')

    warning = get_destructive_command_warning(stripped)
    if warning is not None:
        return SecurityResult(SecurityBehavior.DENY, warning)

    return SecurityResult(SecurityBehavior.ALLOW, 'Command passed security checks')


def get_destructive_command_warning(command: str) -> str | None:
    """匹配命令中的破坏性特征并返回告警文本。

    Args:
        command (str): 待检查的 shell 命令。
    Returns:
        str | None: 命中风险模式时返回告警文本，否则返回 None。
    """
    lowered = command.lower()
    for pattern, warning in _DESTRUCTIVE_PATTERNS:
        if pattern.search(lowered):
            return warning
    return None


def is_command_read_only(command: str) -> bool:
    """粗略判断一条命令链是否处于只读命令子集。

    Args:
        command (str): 待检查的 shell 命令。
    Returns:
        bool: 若整体可视为只读命令链则返回 True。
    """
    segments = split_command(command)
    if not segments:
        return True

    for segment in segments:
        tokens = segment.split()
        if not tokens:
            continue
        head = tokens[0].lower()
        if head == 'git':
            sub = tokens[1].lower() if len(tokens) > 1 else ''
            if sub not in {'status', 'log', 'show', 'diff', 'branch'}:
                return False
            continue
        if head not in _READ_ONLY_COMMANDS:
            return False
    return True


def _contains_control_characters(command: str) -> bool:
    """检查命令是否包含不可见控制字符。

    Args:
        command (str): 待检查的 shell 命令。
    Returns:
        bool: 命中控制字符时返回 True。
    """
    return bool(_CONTROL_CHAR_RE.search(command))


def _match_command_substitution(command: str) -> str | None:
    """匹配可能隐藏附加执行行为的替换语法。

    Args:
        command (str): 待检查的 shell 命令。
    Returns:
        str | None: 命中的替换语法描述，否则返回 None。
    """
    for pattern, description in _SUBSTITUTION_PATTERNS:
        if pattern.search(command):
            return description
    return None
