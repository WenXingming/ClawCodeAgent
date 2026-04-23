"""ISSUE-004 基础工具集与执行上下文实现。

这个模块只负责本地文件工具能力，目标是提供：
1) 简单可调用的工具注册表。
2) 明确的执行上下文（工作目录、权限、输出限制）。
3) 统一的结构化错误返回。

当前仅实现四个基础工具：list_dir/read_file/write_file/edit_file。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .bash_security import check_shell_security
from .core_contracts import (
    AgentPermissions,
    AgentRuntimeConfig,
    JSONDict,
    ToolExecutionResult,
)


class ToolPermissionError(RuntimeError):
    """当工具执行被权限策略拒绝时抛出。"""


class ToolExecutionError(RuntimeError):
    """当工具参数或执行过程不合法时抛出。"""


@dataclass(frozen=True)
class ToolExecutionContext:
    """工具执行上下文。"""

    root: Path  # 工作区根目录。
    command_timeout_seconds: float  # 命令超时时间（为后续工具保留）。
    max_output_chars: int  # 工具文本输出最大长度。
    permissions: AgentPermissions  # 当前会话权限开关。
    tool_registry: dict[str, 'AgentTool'] | None = None  # 当前可用工具映射（可选）。


@dataclass(frozen=True)
class ToolStreamUpdate:
    """工具流式执行更新。"""

    kind: str  # 更新类型：stdout/stderr/result。
    chunk: str = ''  # 增量文本片段。
    result: ToolExecutionResult | None = None  # 最终结果事件携带的结果对象。
    metadata: JSONDict = field(default_factory=dict)  # 可选元数据。


ToolHandler = Callable[
    [JSONDict, ToolExecutionContext],
    str | tuple[str, JSONDict],
]


@dataclass(frozen=True)
class AgentTool:
    """单个工具定义。"""

    name: str  # 工具名称。
    description: str  # 工具简述。
    parameters: JSONDict  # 工具参数 JSON Schema。
    handler: ToolHandler  # 工具处理函数。

    def to_openai_tool(self) -> JSONDict:
        """转换为 OpenAI-compatible tool 定义。"""
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.description,
                'parameters': dict(self.parameters),
            },
        }

    def execute(self, arguments: JSONDict, context: ToolExecutionContext) -> ToolExecutionResult:
        """执行工具并统一封装返回结构。"""
        try:
            result = self.handler(arguments, context)
            if isinstance(result, tuple):
                content, metadata = result
            else:
                content, metadata = result, {}
            return ToolExecutionResult(
                name=self.name,
                ok=True,
                content=content,
                metadata=metadata,
            )
        except ToolPermissionError as exc:
            return ToolExecutionResult(
                name=self.name,
                ok=False,
                content=str(exc),
                metadata={'error_kind': 'permission_denied'},
            )
        except (ToolExecutionError, OSError, UnicodeError) as exc:
            return ToolExecutionResult(
                name=self.name,
                ok=False,
                content=str(exc),
                metadata={'error_kind': 'tool_execution_error'},
            )


@dataclass
class _FileEditRequest:
    """edit_file 的标准化请求。"""

    path: str  # 目标文件路径。
    old_text: str  # 待替换旧文本。
    new_text: str  # 新文本。
    replace_all: bool = False  # 是否替换全部匹配。


@dataclass
class _TextSlice:
    """read_file 的标准化行切片参数。"""

    start_line: int | None = None  # 起始行（1-based，含边界）。
    end_line: int | None = None  # 结束行（1-based，含边界）。


def build_tool_context(
    config: AgentRuntimeConfig,
    *,
    tool_registry: dict[str, AgentTool] | None = None,
) -> ToolExecutionContext:
    """根据运行配置构建工具执行上下文。"""
    return ToolExecutionContext(
        root=config.cwd.resolve(),
        command_timeout_seconds=config.command_timeout_seconds,
        max_output_chars=config.max_output_chars,
        permissions=config.permissions,
        tool_registry=tool_registry,
    )


def execute_tool(
    tool_registry: dict[str, AgentTool],
    name: str,
    arguments: JSONDict,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """按工具名执行一次工具调用。"""
    tool = tool_registry.get(name)
    if tool is None:
        return ToolExecutionResult(
            name=name,
            ok=False,
            content=f'Unknown tool: {name}',
            metadata={'error_kind': 'unknown_tool'},
        )
    return tool.execute(arguments, context)


def execute_tool_streaming(
    tool_registry: dict[str, AgentTool],
    name: str,
    arguments: JSONDict,
    context: ToolExecutionContext,
) -> Iterator[ToolStreamUpdate]:
    """按工具名执行一次工具调用，并输出流式更新。"""
    tool = tool_registry.get(name)
    if tool is None:
        yield ToolStreamUpdate(
            kind='result',
            result=ToolExecutionResult(
                name=name,
                ok=False,
                content=f'Unknown tool: {name}',
                metadata={'error_kind': 'unknown_tool'},
            ),
        )
        return

    if name != 'bash':
        yield ToolStreamUpdate(kind='result', result=tool.execute(arguments, context))
        return

    try:
        yield from _run_bash_stream(arguments, context)
    except ToolPermissionError as exc:
        yield ToolStreamUpdate(
            kind='result',
            result=ToolExecutionResult(
                name='bash',
                ok=False,
                content=str(exc),
                metadata={'error_kind': 'permission_denied'},
            ),
        )
    except (ToolExecutionError, OSError, UnicodeError) as exc:
        yield ToolStreamUpdate(
            kind='result',
            result=ToolExecutionResult(
                name='bash',
                ok=False,
                content=str(exc),
                metadata={'error_kind': 'tool_execution_error'},
            ),
        )


def default_tool_registry() -> dict[str, AgentTool]:
    """返回 ISSUE-004 的最小工具注册表。"""
    tools = [
        AgentTool(
            name='list_dir',
            description='列出工作区目录下的文件和子目录。',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'max_entries': {'type': 'integer', 'minimum': 1, 'maximum': 500},
                },
            },
            handler=_list_dir,
        ),
        AgentTool(
            name='read_file',
            description='读取工作区内文本文件，可选按行区间截取。',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'start_line': {'type': 'integer', 'minimum': 1},
                    'end_line': {'type': 'integer', 'minimum': 1},
                },
                'required': ['path'],
            },
            handler=_read_file,
        ),
        AgentTool(
            name='write_file',
            description='写入工作区文件，不存在时会自动创建父目录。',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'content': {'type': 'string'},
                },
                'required': ['path', 'content'],
            },
            handler=_write_file,
        ),
        AgentTool(
            name='edit_file',
            description='在工作区文件内替换精确文本，默认只替换首个匹配。',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'old_text': {'type': 'string'},
                    'new_text': {'type': 'string'},
                    'replace_all': {'type': 'boolean'},
                },
                'required': ['path', 'old_text', 'new_text'],
            },
            handler=_edit_file,
        ),
        AgentTool(
            name='bash',
            description='在当前工作区执行 shell 命令（受权限和安全策略约束）。',
            parameters={
                'type': 'object',
                'properties': {
                    'command': {'type': 'string'},
                },
                'required': ['command'],
            },
            handler=_run_bash,
        ),
    ]
    return {tool.name: tool for tool in tools}


def _list_dir(arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
    """列出目录内容。"""
    raw_path = _get_string(arguments, 'path', default='.')
    max_entries = _get_int(arguments, 'max_entries', default=200, min_value=1, max_value=500)

    target = _resolve_workspace_path(
        context=context,
        raw_path=raw_path,
        must_exist=True,
        expect_dir=True,
    )

    children = sorted(
        target.iterdir(),
        key=lambda item: (not item.is_dir(), item.name.lower()),
    )

    entries: list[str] = []
    for item in children[:max_entries]:
        suffix = '/' if item.is_dir() else ''
        entries.append(f'- {item.name}{suffix}')

    rel = _to_relative_display(target, context.root)
    lines = [f'# list_dir: {rel}', '']
    if not entries:
        lines.append('(empty)')
    else:
        lines.extend(entries)

    truncated_by_entry_limit = len(children) > max_entries
    if truncated_by_entry_limit:
        lines.append('')
        lines.append(f'... omitted {len(children) - max_entries} entries')

    content = '\n'.join(lines)
    output = _truncate_output(content, context.max_output_chars)
    return (
        output,
        {
            'action': 'list_dir',
            'path': rel,
            'entry_count': len(children),
            'returned_entries': len(entries),
            'entry_limit': max_entries,
            'truncated_by_entry_limit': truncated_by_entry_limit,
            'truncated_by_output_limit': len(content) > len(output),
        },
    )


def _read_file(arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
    """读取文件内容。"""
    raw_path = _require_string(arguments, 'path')
    line_slice = _parse_line_slice(arguments)

    target = _resolve_workspace_path(
        context=context,
        raw_path=raw_path,
        must_exist=True,
        expect_file=True,
    )

    text = target.read_text(encoding='utf-8')
    sliced_text = _slice_text_by_line(text, line_slice)

    output = _truncate_output(sliced_text, context.max_output_chars)
    rel = _to_relative_display(target, context.root)
    return (
        output,
        {
            'action': 'read_file',
            'path': rel,
            'start_line': line_slice.start_line,
            'end_line': line_slice.end_line,
            'returned_chars': len(sliced_text),
            'truncated_by_output_limit': len(sliced_text) > len(output),
        },
    )


def _write_file(arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
    """写入文件。"""
    _ensure_write_allowed(context)

    raw_path = _require_string(arguments, 'path')
    content = _require_string(arguments, 'content')

    target = _resolve_workspace_path(
        context=context,
        raw_path=raw_path,
        must_exist=False,
    )
    if target.exists() and target.is_dir():
        raise ToolExecutionError(f'Path points to a directory, not a file: {raw_path}')

    before_exists = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')

    rel = _to_relative_display(target, context.root)
    return (
        f'Wrote {rel} ({len(content)} chars).',
        {
            'action': 'write_file',
            'path': rel,
            'before_exists': before_exists,
            'char_count': len(content),
        },
    )


def _edit_file(arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
    """替换文件中的文本。"""
    _ensure_write_allowed(context)

    request = _parse_edit_request(arguments)
    target = _resolve_workspace_path(
        context=context,
        raw_path=request.path,
        must_exist=True,
        expect_file=True,
    )

    original = target.read_text(encoding='utf-8')
    if request.old_text not in original:
        raise ToolExecutionError('old_text not found in target file')

    if request.replace_all:
        replaced_count = original.count(request.old_text)
        updated = original.replace(request.old_text, request.new_text)
    else:
        replaced_count = 1
        updated = original.replace(request.old_text, request.new_text, 1)

    target.write_text(updated, encoding='utf-8')

    rel = _to_relative_display(target, context.root)
    return (
        f'Edited {rel}, replaced {replaced_count} occurrence(s).',
        {
            'action': 'edit_file',
            'path': rel,
            'replace_all': request.replace_all,
            'replaced_count': replaced_count,
            'before_chars': len(original),
            'after_chars': len(updated),
        },
    )


def _run_bash(arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
    """执行 shell 命令并返回结构化结果。"""
    command = _require_string(arguments, 'command')
    _ensure_shell_allowed(command, context)

    stdout, stderr, exit_code = _execute_shell_command(command, context)
    rendered = _render_shell_output(stdout, stderr, exit_code)
    output = _truncate_output(rendered, context.max_output_chars)

    return (
        output,
        {
            'action': 'bash',
            'command': command,
            'exit_code': exit_code,
            'stdout_chars': len(stdout),
            'stderr_chars': len(stderr),
            'truncated_by_output_limit': len(rendered) > len(output),
        },
    )


def _run_bash_stream(
    arguments: JSONDict,
    context: ToolExecutionContext,
) -> Iterator[ToolStreamUpdate]:
    """执行 shell 命令并输出 stdout/stderr 增量与最终结果。"""
    command = _require_string(arguments, 'command')
    _ensure_shell_allowed(command, context)

    stdout, stderr, exit_code = _execute_shell_command(command, context)

    for chunk in _iter_output_chunks(stdout):
        yield ToolStreamUpdate(kind='stdout', chunk=chunk)
    for chunk in _iter_output_chunks(stderr):
        yield ToolStreamUpdate(kind='stderr', chunk=chunk)

    rendered = _render_shell_output(stdout, stderr, exit_code)
    output = _truncate_output(rendered, context.max_output_chars)
    yield ToolStreamUpdate(
        kind='result',
        result=ToolExecutionResult(
            name='bash',
            ok=True,
            content=output,
            metadata={
                'action': 'bash',
                'command': command,
                'exit_code': exit_code,
                'stdout_chars': len(stdout),
                'stderr_chars': len(stderr),
                'truncated_by_output_limit': len(rendered) > len(output),
            },
        ),
    )


def _execute_shell_command(command: str, context: ToolExecutionContext) -> tuple[str, str, int]:
    """执行 shell 命令并返回 stdout/stderr/exit_code。"""
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=context.root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    try:
        stdout, stderr = process.communicate(timeout=context.command_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        try:
            process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            # 进程被 kill 后仍未及时回收时，直接进入统一超时错误返回。
            pass
        raise ToolExecutionError(
            f'Shell command timed out after {context.command_timeout_seconds} seconds'
        ) from exc

    return stdout or '', stderr or '', int(process.returncode)


def _render_shell_output(stdout: str, stderr: str, exit_code: int) -> str:
    """统一 shell 输出文本格式。"""
    lines = [
        f'exit_code={exit_code}',
        '[stdout]',
        stdout.rstrip(),
        '[stderr]',
        stderr.rstrip(),
    ]
    return '\n'.join(lines).strip()


def _ensure_write_allowed(context: ToolExecutionContext) -> None:
    """统一写权限检查。"""
    if context.permissions.allow_file_write:
        return
    raise ToolPermissionError('File write permission denied: allow_file_write=false')


def _ensure_shell_allowed(command: str, context: ToolExecutionContext) -> None:
    """统一 shell 权限与安全策略检查。"""
    allowed, reason = check_shell_security(
        command,
        allow_shell=context.permissions.allow_shell_commands,
        allow_destructive=context.permissions.allow_destructive_shell_commands,
    )
    if allowed:
        return
    raise ToolPermissionError(f'Shell command blocked: {reason}')


def _parse_edit_request(arguments: JSONDict) -> _FileEditRequest:
    """解析 edit_file 参数。"""
    old_text = _require_string(arguments, 'old_text')
    if not old_text:
        raise ToolExecutionError('old_text cannot be empty')

    return _FileEditRequest(
        path=_require_string(arguments, 'path'),
        old_text=old_text,
        new_text=_require_string(arguments, 'new_text'),
        replace_all=_get_bool(arguments, 'replace_all', default=False),
    )


def _parse_line_slice(arguments: JSONDict) -> _TextSlice:
    """解析 read_file 行切片参数。"""
    start_line = _get_optional_int(arguments, 'start_line', min_value=1)
    end_line = _get_optional_int(arguments, 'end_line', min_value=1)

    if start_line is not None and end_line is not None and end_line < start_line:
        raise ToolExecutionError('end_line must be greater than or equal to start_line')

    return _TextSlice(start_line=start_line, end_line=end_line)


def _slice_text_by_line(text: str, line_slice: _TextSlice) -> str:
    """按 1-based 行号截取文本。"""
    if line_slice.start_line is None and line_slice.end_line is None:
        return text

    lines = text.splitlines(keepends=True)
    if not lines:
        return ''

    start = line_slice.start_line or 1
    end = line_slice.end_line or len(lines)
    return ''.join(lines[start - 1:end])


def _resolve_workspace_path(
    *,
    context: ToolExecutionContext,
    raw_path: str,
    must_exist: bool,
    expect_file: bool = False,
    expect_dir: bool = False,
) -> Path:
    """解析并校验路径必须位于工作区内。"""
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (context.root / candidate).resolve()

    try:
        resolved.relative_to(context.root)
    except ValueError as exc:
        raise ToolExecutionError(f'Path escapes workspace root: {raw_path}') from exc

    if must_exist and not resolved.exists():
        raise ToolExecutionError(f'Path does not exist: {raw_path}')

    if expect_file and resolved.exists() and not resolved.is_file():
        raise ToolExecutionError(f'Path is not a file: {raw_path}')

    if expect_dir and resolved.exists() and not resolved.is_dir():
        raise ToolExecutionError(f'Path is not a directory: {raw_path}')

    return resolved


def _truncate_output(text: str, limit: int) -> str:
    """按上限截断输出，保留头尾信息。"""
    if limit <= 0 or len(text) <= limit:
        return text

    half = max(1, limit // 2)
    head = text[:half]
    tail = text[-half:]
    return f'{head}\n...[output truncated, total {len(text)} chars]...\n{tail}'


def _to_relative_display(path: Path, root: Path) -> str:
    """把绝对路径转换为工作区内相对显示路径。"""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    text = str(relative)
    return text if text else '.'


def _iter_output_chunks(text: str, chunk_size: int = 512) -> Iterator[str]:
    """把输出按固定大小切成可回放片段。"""
    if not text:
        return
    for start in range(0, len(text), chunk_size):
        yield text[start : start + chunk_size]


def _require_string(arguments: JSONDict, key: str) -> str:
    """读取必填字符串参数。"""
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ToolExecutionError(f'Argument "{key}" must be a string')
    return value


def _get_string(arguments: JSONDict, key: str, *, default: str) -> str:
    """读取可选字符串参数。"""
    value = arguments.get(key, default)
    if not isinstance(value, str):
        raise ToolExecutionError(f'Argument "{key}" must be a string')
    return value


def _get_bool(arguments: JSONDict, key: str, *, default: bool) -> bool:
    """读取可选布尔参数。"""
    value = arguments.get(key, default)
    if not isinstance(value, bool):
        raise ToolExecutionError(f'Argument "{key}" must be a boolean')
    return value


def _get_int(
    arguments: JSONDict,
    key: str,
    *,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    """读取可选整数参数并校验范围。"""
    value = arguments.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolExecutionError(f'Argument "{key}" must be an integer')

    if min_value is not None and value < min_value:
        raise ToolExecutionError(f'Argument "{key}" must be >= {min_value}')
    if max_value is not None and value > max_value:
        raise ToolExecutionError(f'Argument "{key}" must be <= {max_value}')
    return value


def _get_optional_int(
    arguments: JSONDict,
    key: str,
    *,
    min_value: int | None = None,
) -> int | None:
    """读取可选整数参数。"""
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolExecutionError(f'Argument "{key}" must be an integer')
    if min_value is not None and value < min_value:
        raise ToolExecutionError(f'Argument "{key}" must be >= {min_value}')
    return value
