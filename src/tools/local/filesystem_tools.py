"""本地文件系统工具集合。

提供 list_dir、read_file、write_file、edit_file 四个基础文件系统工具，
所有工具通过 build_filesystem_tools 工厂函数产出 ToolDescriptor 注册表条目。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core_contracts.primitives import JSONDict
from core_contracts.tools import ToolDescriptor
from tools.executor import ToolExecutionContext, ToolExecutionError, ToolPermissionError


@dataclass
class _FileEditRequest:
    """表示 edit_file 的归一化请求参数。"""

    path: str  # str: 目标文件路径。
    old_text: str  # str: 待替换的原始文本。
    new_text: str  # str: 替换后的新文本。
    replace_all: bool = False  # bool: 是否替换所有匹配项。


@dataclass
class _TextSlice:
    """表示 read_file 的 1-based 行切片范围。"""

    start_line: int | None = None  # int | None: 起始行号，None 表示从第一行开始。
    end_line: int | None = None  # int | None: 结束行号，None 表示到末行。


def build_filesystem_tools() -> tuple[ToolDescriptor, ...]:
    """构建基础文件系统工具定义。
    Args:
        None: 该方法不接收参数。
    Returns:
        tuple[ToolDescriptor, ...]: list_dir, read_file, write_file, edit_file 的工具描述符元组。
    """
    return (
        ToolDescriptor(
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
        ToolDescriptor(
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
        ToolDescriptor(
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
        ToolDescriptor(
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
    )


def _list_dir(arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
    """列出工作区内目录内容。
    Args:
        arguments (JSONDict): 包含 path 和 max_entries 的工具参数。
        context (ToolExecutionContext): 工具执行上下文。
    Returns:
        str | tuple[str, JSONDict]: 目录列表文本及元数据。
    Raises:
        ToolExecutionError: 当路径解析非法或越界时抛出。
        ToolPermissionError: 当工具权限不足时抛出。
    """
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
    """读取工作区内文本文件，可选按行裁剪。
    Args:
        arguments (JSONDict): 包含 path、start_line、end_line 的工具参数。
        context (ToolExecutionContext): 工具执行上下文。
    Returns:
        str | tuple[str, JSONDict]: 文件内容及切片元数据。
    Raises:
        ToolExecutionError: 当路径非法或行区间错误时抛出。
    """
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


def _parse_line_slice(arguments: JSONDict) -> _TextSlice:
    """把 start_line 与 end_line 参数归一化为行切片对象。
    Args:
        arguments (JSONDict): 原始工具参数字典。
    Returns:
        _TextSlice: 1-based 行区间切片描述。
    Raises:
        ToolExecutionError: 当 end_line 小于 start_line 时抛出。
    """
    start_line = _get_optional_int(arguments, 'start_line', min_value=1)
    end_line = _get_optional_int(arguments, 'end_line', min_value=1)

    if start_line is not None and end_line is not None and end_line < start_line:
        raise ToolExecutionError('end_line must be greater than or equal to start_line')

    return _TextSlice(start_line=start_line, end_line=end_line)


def _slice_text_by_line(text: str, line_slice: _TextSlice) -> str:
    """按 1-based 闭区间切片截取文本。
    Args:
        text (str): 原始文件全文。
        line_slice (_TextSlice): 目标行区间。
    Returns:
        str: 裁剪后的文本片段。
    """
    if line_slice.start_line is None and line_slice.end_line is None:
        return text

    lines = text.splitlines(keepends=True)
    if not lines:
        return ''

    start = line_slice.start_line or 1
    end = line_slice.end_line or len(lines)
    return ''.join(lines[start - 1:end])


def _write_file(arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
    """写入或创建工作区内文件。
    Args:
        arguments (JSONDict): 包含 path 和 content 的工具参数。
        context (ToolExecutionContext): 工具执行上下文。
    Returns:
        str | tuple[str, JSONDict]: 写入确认文本及元数据。
    Raises:
        ToolExecutionError: 当路径指向目录时抛出。
        ToolPermissionError: 当写入权限未开启时抛出。
    """
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
    """在工作区文件内执行精确文本替换。
    Args:
        arguments (JSONDict): 包含 path、old_text、new_text、replace_all 的工具参数。
        context (ToolExecutionContext): 工具执行上下文。
    Returns:
        str | tuple[str, JSONDict]: 替换确认文本及统计元数据。
    Raises:
        ToolExecutionError: 当 old_text 未找到或为空时抛出。
        ToolPermissionError: 当写入权限未开启时抛出。
    """
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


def _ensure_write_allowed(context: ToolExecutionContext) -> None:
    """检查当前上下文是否允许写文件。
    Args:
        context (ToolExecutionContext): 工具执行上下文。
    Returns:
        None: 权限检查通过时无返回值。
    Raises:
        ToolPermissionError: 当 allow_file_write 为 False 时抛出。
    """
    if context.permissions.allow_file_write:
        return
    raise ToolPermissionError('File write permission denied: allow_file_write=false')


def _parse_edit_request(arguments: JSONDict) -> _FileEditRequest:
    """把 edit_file 参数归一化为内部请求对象。
    Args:
        arguments (JSONDict): 原始工具参数字典。
    Returns:
        _FileEditRequest: 归一化后的编辑请求。
    Raises:
        ToolExecutionError: 当 old_text 为空字符串时抛出。
    """
    old_text = _require_string(arguments, 'old_text')
    if not old_text:
        raise ToolExecutionError('old_text cannot be empty')

    return _FileEditRequest(
        path=_require_string(arguments, 'path'),
        old_text=old_text,
        new_text=_require_string(arguments, 'new_text'),
        replace_all=_get_bool(arguments, 'replace_all', default=False),
    )


def _resolve_workspace_path(
    *,
    context: ToolExecutionContext,
    raw_path: str,
    must_exist: bool,
    expect_file: bool = False,
    expect_dir: bool = False,
) -> Path:
    """解析路径并强制其位于工作区根目录之内。
    Args:
        context (ToolExecutionContext): 工具执行上下文。
        raw_path (str): 用户传入的原始路径字符串。
        must_exist (bool): 是否要求路径已存在。
        expect_file (bool): 若存在，是否要求为普通文件。
        expect_dir (bool): 若存在，是否要求为目录。
    Returns:
        Path: 解析后的绝对路径。
    Raises:
        ToolExecutionError: 当路径越界、不存在或类型不匹配时抛出。
    """
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
    """按上限裁剪输出，同时尽量保留头尾信息。
    Args:
        text (str): 原始输出文本。
        limit (int): 最大允许字符数。
    Returns:
        str: 裁剪后的文本，超限时在中间插入省略提示。
    """
    if limit <= 0 or len(text) <= limit:
        return text

    half = max(1, limit // 2)
    head = text[:half]
    tail = text[-half:]
    return f'{head}\n...[output truncated, total {len(text)} chars]...\n{tail}'


def _to_relative_display(path: Path, root: Path) -> str:
    """把绝对路径转换为工作区内相对显示路径。
    Args:
        path (Path): 待转换的绝对路径。
        root (Path): 工作区根目录。
    Returns:
        str: 相对于根目录的路径字符串；根目录自身显示为 '.'。
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    text = str(relative)
    return text if text else '.'


def _require_string(arguments: JSONDict, key: str) -> str:
    """读取必填字符串参数。
    Args:
        arguments (JSONDict): 工具参数字典。
        key (str): 参数名。
    Returns:
        str: 参数值。
    Raises:
        ToolExecutionError: 当参数不存在或类型非字符串时抛出。
    """
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ToolExecutionError(f'Argument "{key}" must be a string')
    return value


def _get_string(arguments: JSONDict, key: str, *, default: str) -> str:
    """读取可选字符串参数，并在缺失时回退默认值。
    Args:
        arguments (JSONDict): 工具参数字典。
        key (str): 参数名。
        default (str): 缺失时的默认值。
    Returns:
        str: 参数值。
    Raises:
        ToolExecutionError: 当参数存在但类型非字符串时抛出。
    """
    value = arguments.get(key, default)
    if not isinstance(value, str):
        raise ToolExecutionError(f'Argument "{key}" must be a string')
    return value


def _get_bool(arguments: JSONDict, key: str, *, default: bool) -> bool:
    """读取可选布尔参数，并在缺失时回退默认值。
    Args:
        arguments (JSONDict): 工具参数字典。
        key (str): 参数名。
        default (bool): 缺失时的默认值。
    Returns:
        bool: 参数值。
    Raises:
        ToolExecutionError: 当参数存在但类型非布尔时抛出。
    """
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
    """读取整数参数并校验取值范围。
    Args:
        arguments (JSONDict): 工具参数字典。
        key (str): 参数名。
        default (int): 缺失时的默认值。
        min_value (int | None): 允许的最小值。
        max_value (int | None): 允许的最大值。
    Returns:
        int: 校验后的整数参数值。
    Raises:
        ToolExecutionError: 当参数类型非整数或取值越界时抛出。
    """
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
    """读取可选整数参数。
    Args:
        arguments (JSONDict): 工具参数字典。
        key (str): 参数名。
        min_value (int | None): 允许的最小值。
    Returns:
        int | None: 参数值；未提供时返回 None。
    Raises:
        ToolExecutionError: 当参数存在但类型非整数或取值越界时抛出。
    """
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolExecutionError(f'Argument "{key}" must be an integer')
    if min_value is not None and value < min_value:
        raise ToolExecutionError(f'Argument "{key}" must be >= {min_value}')
    return value
