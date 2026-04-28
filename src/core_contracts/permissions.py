"""工具权限策略契约。"""

from __future__ import annotations

from dataclasses import dataclass

from .coercion import _as_bool, _as_dict, _first_present
from .protocol import JSONDict


@dataclass(frozen=True)
class ToolPermissionPolicy:
    """运行时和工具执行使用的权限开关。"""

    allow_file_write: bool = False
    allow_shell_commands: bool = False
    allow_destructive_shell_commands: bool = False

    def to_dict(self) -> JSONDict:
        return {
            'allow_file_write': self.allow_file_write,
            'allow_shell_commands': self.allow_shell_commands,
            'allow_destructive_shell_commands': self.allow_destructive_shell_commands,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ToolPermissionPolicy':
        data = _as_dict(payload)
        return cls(
            allow_file_write=_as_bool(_first_present(data, 'allow_file_write', 'allowFileWrite'), False),
            allow_shell_commands=_as_bool(_first_present(data, 'allow_shell_commands', 'allowShellCommands'), False),
            allow_destructive_shell_commands=_as_bool(
                _first_present(
                    data,
                    'allow_destructive_shell_commands',
                    'allowDestructiveShellCommands',
                ),
                False,
            ),
        )