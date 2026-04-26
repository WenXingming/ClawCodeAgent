"""ISSUE-005 Shell 工具安全策略。

本模块只做一件事：判断命令是否允许执行。
优先级顺序：
1) 是否允许 shell。
2) 是否包含高风险模式（破坏性命令、命令替换、控制字符）。
3) 是否允许 destructive。

设计目标：规则清晰、行为稳定、容易测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SecurityBehavior(Enum):
    """安全检查结果类型。"""

    ALLOW = 'allow' # 允许执行，且不包含明显风险特征
    DENY = 'deny' # 明确拒绝执行，包含高风险特征
    PASSTHROUGH = 'passthrough' # 不明确允许或拒绝，需结合权限设置综合判断（目前未使用）


@dataclass(frozen=True)
class SecurityResult:
    """单次安全检查结果。"""

    behavior: SecurityBehavior  # 安全行为。
    message: str  # 可读解释。


# 控制字符检测（允许 \t/\n/\r）。
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

# 命令替换 / 进程替换等高风险语法。
_SUBSTITUTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'\$\('), '$() command substitution'),
    (re.compile(r'`'), '`...` command substitution'),
    (re.compile(r'<\('), '<() process substitution'),
    (re.compile(r'>\('), '>() process substitution'),
    (re.compile(r'\$\{'), '${} parameter expansion'),
]

# 破坏性命令匹配规则。
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

# 只读命令白名单（用于辅助判断，不作为最终准入唯一依据）。
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
    """按 ; && || | 粗粒度拆分命令段，保留引号内内容。

    Args:
        command (str): 原始命令行字符串。

    Returns:
        list[str]: 拆分后的命令片段列表（已去除空白片段）。
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


def _contains_control_characters(command: str) -> bool:
    """检测不可见控制字符。"""
    return bool(_CONTROL_CHAR_RE.search(command))


def _match_command_substitution(command: str) -> str | None:
    """匹配命令替换模式。

    Args:
        command (str): 待检测命令。

    Returns:
        str | None: 命中的风险描述，未命中则返回 None。
    """
    for pattern, description in _SUBSTITUTION_PATTERNS:
        if pattern.search(command):
            return description
    return None


def get_destructive_command_warning(command: str) -> str | None:
    """匹配破坏性命令模式。

    Args:
        command (str): 待检测命令。

    Returns:
        str | None: 命中时返回告警文案，否则返回 None。
    """
    lowered = command.lower()
    for pattern, warning in _DESTRUCTIVE_PATTERNS:
        if pattern.search(lowered):
            return warning
    return None


def is_command_read_only(command: str) -> bool:
    """判断命令是否整体可视为只读。

    Args:
        command (str): 待检测命令。

    Returns:
        bool: 是否可判定为只读命令集合。
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
            # 只允许常见只读子命令。
            sub = tokens[1].lower() if len(tokens) > 1 else ''
            if sub not in {'status', 'log', 'show', 'diff', 'branch'}:
                return False
            continue
        if head not in _READ_ONLY_COMMANDS:
            return False
    return True


def bash_command_is_safe(command: str) -> SecurityResult:
    """判断 shell 命令是否安全。

    Args:
        command (str): 待检测命令。

    Returns:
        SecurityResult: 安全判定结果与解释信息。
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


def check_shell_security(
    command: str,
    *,
    allow_shell: bool,
    allow_destructive: bool,
) -> tuple[bool, str]:
    """综合权限与命令特征，返回是否允许执行。

    Args:
        command (str): 待执行命令。
        allow_shell (bool): 是否启用 shell 能力。
        allow_destructive (bool): 是否允许破坏性命令。

    Returns:
        tuple[bool, str]: (是否允许, 拒绝原因)。
    """
    if not allow_shell:
        return False, 'Shell commands are disabled: allow_shell_commands=false'

    result = bash_command_is_safe(command)
    if result.behavior == SecurityBehavior.DENY and not allow_destructive:
        return False, result.message

    return True, ''
