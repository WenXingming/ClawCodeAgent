"""Shell 命令安全策略。

该模块负责在真正执行 shell 之前做静态风险检查，并把权限开关与命令特征
组合为统一的允许/拒绝结果，供 local_tools 中的 bash 工具复用。
所有安全规则封装在 ShellSecurityPolicy 类中，外部仅通过 check_shell_security
作为主入口消费。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class ShellSecurityPolicy:
    """封装 shell 风险检查规则与权限组合逻辑。

    该类维护控制字符、命令替换、破坏性命令与只读命令四套规则，
    对外仅暴露 check_shell_security 作为权限联合判断入口。
    """

    control_char_re: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
    )  # re.Pattern[str]: 匹配不可见控制字符的正则。
    substitution_patterns: tuple[tuple[re.Pattern[str], str], ...] = field(
        default_factory=lambda: (
            (re.compile(r'\$\('), '$() command substitution'),
            (re.compile(r'`'), '`...` command substitution'),
            (re.compile(r'<\('), '<() process substitution'),
            (re.compile(r'>\('), '>() process substitution'),
            (re.compile(r'\$\{'), '${} parameter expansion'),
        )
    )  # tuple[tuple[re.Pattern[str], str], ...]: 命令替换模式与描述。
    destructive_patterns: tuple[tuple[re.Pattern[str], str], ...] = field(
        default_factory=lambda: (
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
        )
    )  # tuple[tuple[re.Pattern[str], str], ...]: 破坏性命令模式与告警。
    read_only_commands: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                'cat', 'type', 'head', 'tail', 'more', 'less',
                'grep', 'rg', 'findstr', 'ls', 'dir', 'tree',
                'pwd', 'cd', 'echo', 'printf', 'whoami',
                'hostname', 'date', 'time', 'git',
            }
        )
    )  # frozenset[str]: 只读命令白名单。

    # ── 主入口 ─────────────────────────────────────────────────────

    def check_shell_security(
        self,
        command: str,
        *,
        allow_shell: bool,
        allow_destructive: bool,
    ) -> tuple[bool, str]:
        """结合权限开关与命令风险判断是否允许执行。
        Args:
            command (str): 待检查的原始命令字符串。
            allow_shell (bool): 是否启用了 shell 命令权限。
            allow_destructive (bool): 是否允许破坏性命令。
        Returns:
            tuple[bool, str]: (是否允许, 拒绝原因) 元组；允许时原因为空串。
        """
        if not allow_shell:
            return False, 'Shell commands are disabled: allow_shell_commands=false'

        result = self.analyze_command(command)
        if result.behavior == SecurityBehavior.DENY and not allow_destructive:
            return False, result.message

        return True, ''

    # ── 命令分析 ────────────────────────────────────────────────────

    def analyze_command(self, command: str) -> SecurityResult:
        """仅基于命令文本判断 shell 命令是否安全。
        Args:
            command (str): 待分析的命令文本。
        Returns:
            SecurityResult: 安全检查的结构化结果。
        """
        stripped = command.strip()
        if not stripped:
            return SecurityResult(SecurityBehavior.ALLOW, 'Empty command is safe')

        if self._contains_control_characters(stripped):
            return SecurityResult(SecurityBehavior.DENY, 'Command contains control characters')

        substitution = self._match_command_substitution(stripped)
        if substitution is not None:
            return SecurityResult(SecurityBehavior.DENY, f'Command contains {substitution}')

        warning = self.get_destructive_command_warning(stripped)
        if warning is not None:
            return SecurityResult(SecurityBehavior.DENY, warning)

        return SecurityResult(SecurityBehavior.ALLOW, 'Command passed security checks')

    def _contains_control_characters(self, command: str) -> bool:
        """检查命令是否包含不可见控制字符。
        Args:
            command (str): 待检查的命令文本。
        Returns:
            bool: 存在控制字符时为 True。
        """
        return bool(self.control_char_re.search(command))

    def _match_command_substitution(self, command: str) -> str | None:
        """匹配可能隐藏附加执行行为的替换语法。
        Args:
            command (str): 待检查的命令文本。
        Returns:
            str | None: 匹配到的替换模式描述；无匹配时返回 None。
        """
        for pattern, description in self.substitution_patterns:
            if pattern.search(command):
                return description
        return None

    def get_destructive_command_warning(self, command: str) -> str | None:
        """匹配命令中的破坏性特征并返回告警文本。
        Args:
            command (str): 待检查的命令文本。
        Returns:
            str | None: 匹配到的破坏性告警；无匹配时返回 None。
        """
        lowered = command.lower()
        for pattern, warning in self.destructive_patterns:
            if pattern.search(lowered):
                return warning
        return None

    # ── 只读判定 ────────────────────────────────────────────────────

    def is_command_read_only(self, command: str) -> bool:
        """粗略判断一条命令链是否处于只读命令子集。
        Args:
            command (str): 待判断的命令文本。
        Returns:
            bool: 全部段落在只读子集中时为 True。
        """
        segments = self.split_command(command)
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
            if head not in self.read_only_commands:
                return False
        return True

    def split_command(self, command: str) -> list[str]:
        """按链式操作符拆分命令，同时保留引号内文本。
        Args:
            command (str): 原始命令文本。
        Returns:
            list[str]: 按 ; | && || 拆分后的独立命令段列表。
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
