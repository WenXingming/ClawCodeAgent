"""本地工具注册表与执行入口。

该模块统一承载工具定义、执行上下文、标准错误封装以及基础文件与 shell
工具实现，并保持“公有入口在前、处理链局部辅助函数紧跟其后”的阅读顺序。
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .bash_security import ShellSecurityPolicy
from core_contracts.config import AgentPermissions, AgentRuntimeConfig
from core_contracts.protocol import JSONDict, ToolExecutionResult


class ToolPermissionError(RuntimeError):
    """表示工具调用被权限策略拒绝。"""


class ToolExecutionError(RuntimeError):
    """表示工具参数非法或执行过程失败。"""


@dataclass(frozen=True)
class ToolExecutionContext:
    """描述一次工具调用共享的不可变执行上下文。

    上层会在进入工具执行前构造该对象，统一提供工作区根目录、权限开关、
    输出限制和安全环境变量等信息。
    """

    root: Path  # Path: 当前工具调用可见的工作区根目录。
    command_timeout_seconds: float  # float: shell 命令允许执行的最长时间。
    max_output_chars: int  # int: 单次工具调用允许返回的最大文本长度。
    permissions: AgentPermissions  # AgentPermissions: 当前会话的权限开关集合。
    safe_env: dict[str, str] = field(default_factory=dict)  # dict[str, str]: 允许注入 shell 的安全环境变量。
    tool_registry: dict[str, 'LocalTool'] | None = None  # dict[str, LocalTool] | None: 当前可见工具映射。


@dataclass(frozen=True)
class ToolStreamUpdate:
    """表示流式工具调用过程中产出的单个更新事件。"""

    kind: str  # str: 事件类型，通常为 stdout、stderr 或 result。
    chunk: str = ''  # str: 当前增量文本片段。
    result: ToolExecutionResult | None = None  # ToolExecutionResult | None: 最终结果事件携带的结果对象。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict: 附带的可选结构化元数据。


ToolHandler = Callable[
    [JSONDict, ToolExecutionContext],
    str | tuple[str, JSONDict],
]


@dataclass(frozen=True)
class LocalTool:
    """表示单个可暴露给模型的工具定义。

    每个工具对象同时包含 schema 信息和真正的处理函数，便于注册表直接完成
    声明导出与实际执行。
    """

    name: str  # str: 工具名称。
    description: str  # str: 面向模型的工具说明。
    parameters: JSONDict  # JSONDict: 工具参数的 JSON Schema。
    handler: ToolHandler  # ToolHandler: 实际执行该工具的处理函数。

    def to_openai_tool(self) -> JSONDict:
        """把工具定义转换为 OpenAI 兼容的函数 schema。

        Args:
            None: 无参数。
        Returns:
            JSONDict: OpenAI tools 兼容的函数定义对象。
        """
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.description,
                'parameters': dict(self.parameters),
            },
        }

    def execute(self, arguments: JSONDict, context: ToolExecutionContext) -> ToolExecutionResult:
        """执行工具并统一封装成功或失败结果。

        Args:
            arguments (JSONDict): 工具调用参数。
            context (ToolExecutionContext): 当前调用上下文。
        Returns:
            ToolExecutionResult: 统一结构化后的执行结果。
        """
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
    """表示 edit_file 的归一化请求参数。"""

    path: str  # str: 目标文件路径。
    old_text: str  # str: 需要被匹配和替换的旧文本。
    new_text: str  # str: 用于替换的新文本。
    replace_all: bool = False  # bool: 是否替换全部匹配项。


@dataclass
class _TextSlice:
    """表示 read_file 的 1-based 行切片范围。"""

    start_line: int | None = None  # int | None: 起始行号，含边界。
    end_line: int | None = None  # int | None: 结束行号，含边界。


@dataclass(frozen=True)
class LocalToolService:
    """封装本地工具注册、上下文构造与执行流程。"""

    shell_security_policy: ShellSecurityPolicy = field(default_factory=ShellSecurityPolicy)

    def build_context(
        self,
        config: AgentRuntimeConfig,
        *,
        tool_registry: dict[str, LocalTool] | None = None,
        safe_env: dict[str, str] | None = None,
    ) -> ToolExecutionContext:
        """根据运行时配置构造工具执行上下文。"""
        return ToolExecutionContext(
            root=config.cwd.resolve(),
            command_timeout_seconds=config.command_timeout_seconds,
            max_output_chars=config.max_output_chars,
            permissions=config.permissions,
            safe_env=dict(safe_env or {}),
            tool_registry=tool_registry,
        )

    def execute(
        self,
        tool_registry: dict[str, LocalTool],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """按工具名执行一次普通工具调用。"""
        tool = tool_registry.get(name)
        if tool is None:
            return _unknown_tool_result(name)
        return tool.execute(arguments, context)

    def execute_streaming(
        self,
        tool_registry: dict[str, LocalTool],
        name: str,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """按工具名执行一次流式工具调用。"""
        tool = tool_registry.get(name)
        if tool is None:
            yield ToolStreamUpdate(kind='result', result=_unknown_tool_result(name))
            return

        if name != 'bash':
            yield ToolStreamUpdate(kind='result', result=tool.execute(arguments, context))
            return

        try:
            yield from self._run_bash_stream(arguments, context)
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

    def default_registry(self) -> dict[str, LocalTool]:
        """返回内置基础工具注册表。"""
        tools = [
            LocalTool(
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
            LocalTool(
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
            LocalTool(
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
            LocalTool(
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
            LocalTool(
                name='bash',
                description='在当前工作区执行 shell 命令（受权限和安全策略约束）。',
                parameters={
                    'type': 'object',
                    'properties': {
                        'command': {'type': 'string'},
                    },
                    'required': ['command'],
                },
                handler=self._run_bash,
            ),
        ]
        return {tool.name: tool for tool in tools}

    def _run_bash(self, arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
        """执行 shell 命令并返回结构化文本结果。"""
        command = _require_string(arguments, 'command')
        self._ensure_shell_allowed(command, context)

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
        self,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """执行 shell 命令并按 stdout/stderr 分块输出流式事件。"""
        command = _require_string(arguments, 'command')
        self._ensure_shell_allowed(command, context)

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        process = _start_shell_process(command, context)
        start_time = time.monotonic()
        updates: queue.Queue[tuple[str, str]] = queue.Queue()
        reader_threads = [
            threading.Thread(
                target=_drain_process_stream,
                args=(process.stdout, 'stdout', updates),
                daemon=True,
            ),
            threading.Thread(
                target=_drain_process_stream,
                args=(process.stderr, 'stderr', updates),
                daemon=True,
            ),
        ]
        for thread in reader_threads:
            thread.start()

        closed_streams = {'stdout': False, 'stderr': False}
        try:
            while True:
                if time.monotonic() - start_time > context.command_timeout_seconds:
                    _terminate_shell_process(process)
                    raise ToolExecutionError(
                        f'Shell command timed out after {context.command_timeout_seconds} seconds'
                    )

                try:
                    stream_name, chunk = updates.get(timeout=0.05)
                except queue.Empty:
                    if process.poll() is not None and all(closed_streams.values()):
                        break
                    continue

                if stream_name.endswith('_closed'):
                    closed_streams[stream_name[:-7]] = True
                    if process.poll() is not None and all(closed_streams.values()):
                        break
                    continue

                if stream_name == 'stdout':
                    stdout_chunks.append(chunk)
                else:
                    stderr_chunks.append(chunk)
                yield ToolStreamUpdate(kind=stream_name, chunk=chunk)
        finally:
            for thread in reader_threads:
                thread.join(timeout=0.2)

        remaining_timeout = max(0.1, context.command_timeout_seconds - (time.monotonic() - start_time))
        try:
            exit_code = int(process.wait(timeout=remaining_timeout))
        except subprocess.TimeoutExpired as exc:
            _terminate_shell_process(process)
            raise ToolExecutionError(
                f'Shell command timed out after {context.command_timeout_seconds} seconds'
            ) from exc

        stdout = ''.join(stdout_chunks)
        stderr = ''.join(stderr_chunks)

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

    def _ensure_shell_allowed(self, command: str, context: ToolExecutionContext) -> None:
        """检查 shell 权限和命令安全策略。"""
        allowed, reason = self.shell_security_policy.check_shell_security(
            command,
            allow_shell=context.permissions.allow_shell_commands,
            allow_destructive=context.permissions.allow_destructive_shell_commands,
        )
        if allowed:
            return
        raise ToolPermissionError(f'Shell command blocked: {reason}')


def _unknown_tool_result(name: str) -> ToolExecutionResult:
    """为未知工具返回统一的结构化错误结果。"""
    return ToolExecutionResult(
        name=name,
        ok=False,
        content=f'Unknown tool: {name}',
        metadata={'error_kind': 'unknown_tool'},
    )


def _list_dir(arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
    """列出工作区内目录内容。

    Args:
        arguments (JSONDict): 工具调用参数。
        context (ToolExecutionContext): 当前调用上下文。
    Returns:
        str | tuple[str, JSONDict]: 文本结果，或带附加元数据的结果元组。
    Raises:
        ToolExecutionError: 当参数非法、目录不存在或路径越界时抛出。
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
        arguments (JSONDict): 工具调用参数。
        context (ToolExecutionContext): 当前调用上下文。
    Returns:
        str | tuple[str, JSONDict]: 文本结果，或带附加元数据的结果元组。
    Raises:
        ToolExecutionError: 当参数非法、文件不存在或路径越界时抛出。
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
        arguments (JSONDict): 工具调用参数。
    Returns:
        _TextSlice: 归一化后的行切片对象。
    Raises:
        ToolExecutionError: 当行号类型非法或结束行早于开始行时抛出。
    """
    start_line = _get_optional_int(arguments, 'start_line', min_value=1)
    end_line = _get_optional_int(arguments, 'end_line', min_value=1)

    if start_line is not None and end_line is not None and end_line < start_line:
        raise ToolExecutionError('end_line must be greater than or equal to start_line')

    return _TextSlice(start_line=start_line, end_line=end_line)


def _slice_text_by_line(text: str, line_slice: _TextSlice) -> str:
    """按 1-based 闭区间切片截取文本。

    Args:
        text (str): 原始文本内容。
        line_slice (_TextSlice): 目标行切片范围。
    Returns:
        str: 切片后的文本内容。
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
        arguments (JSONDict): 工具调用参数。
        context (ToolExecutionContext): 当前调用上下文。
    Returns:
        str | tuple[str, JSONDict]: 文本结果，或带附加元数据的结果元组。
    Raises:
        ToolPermissionError: 当当前权限不允许写文件时抛出。
        ToolExecutionError: 当参数非法、路径越界或目标路径是目录时抛出。
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
        arguments (JSONDict): 工具调用参数。
        context (ToolExecutionContext): 当前调用上下文。
    Returns:
        str | tuple[str, JSONDict]: 文本结果，或带附加元数据的结果元组。
    Raises:
        ToolPermissionError: 当当前权限不允许写文件时抛出。
        ToolExecutionError: 当参数非法、路径越界或 old_text 未匹配时抛出。
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
        context (ToolExecutionContext): 当前调用上下文。
    Returns:
        None: 无返回值。
    Raises:
        ToolPermissionError: 当 allow_file_write 为 False 时抛出。
    """
    if context.permissions.allow_file_write:
        return
    raise ToolPermissionError('File write permission denied: allow_file_write=false')


def _parse_edit_request(arguments: JSONDict) -> _FileEditRequest:
    """把 edit_file 参数归一化为内部请求对象。

    Args:
        arguments (JSONDict): 工具调用参数。
    Returns:
        _FileEditRequest: 归一化后的编辑请求对象。
    Raises:
        ToolExecutionError: 当字段缺失、类型错误或 old_text 为空时抛出。
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


def _execute_shell_command(command: str, context: ToolExecutionContext) -> tuple[str, str, int]:
    """执行 shell 命令并返回 stdout、stderr 与退出码。

    Args:
        command (str): 待执行的 shell 命令。
        context (ToolExecutionContext): 当前调用上下文。
    Returns:
        tuple[str, str, int]: 依次为 stdout、stderr 和 exit_code。
    Raises:
        ToolExecutionError: 当命令执行超时时抛出。
    """
    process = _start_shell_process(command, context)
    try:
        stdout, stderr = process.communicate(timeout=context.command_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_shell_process(process)
        raise ToolExecutionError(
            f'Shell command timed out after {context.command_timeout_seconds} seconds'
        ) from exc

    return stdout or '', stderr or '', int(process.returncode)


def _start_shell_process(command: str, context: ToolExecutionContext) -> subprocess.Popen[str]:
    """按统一参数启动一个 shell 子进程。"""
    environment = dict(os.environ)
    environment.update(context.safe_env)
    return subprocess.Popen(
        command,
        shell=True,
        cwd=context.root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=environment,
        bufsize=1,
    )


def _terminate_shell_process(process: subprocess.Popen[str]) -> None:
    """终止子进程并尽量回收管道。"""
    process.kill()
    try:
        process.communicate(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass


def _drain_process_stream(
    stream,
    stream_name: str,
    updates: queue.Queue[tuple[str, str]],
) -> None:
    """把子进程 stdout 或 stderr 按行推入线程安全队列。"""
    if stream is None:
        updates.put((f'{stream_name}_closed', ''))
        return

    try:
        for line in iter(stream.readline, ''):
            if not line:
                break
            updates.put((stream_name, line))
    finally:
        stream.close()
        updates.put((f'{stream_name}_closed', ''))


def _render_shell_output(stdout: str, stderr: str, exit_code: int) -> str:
    """把 shell 执行结果渲染成 transcript 友好的文本格式。

    Args:
        stdout (str): 标准输出文本。
        stderr (str): 标准错误文本。
        exit_code (int): 进程退出码。
    Returns:
        str: 标准化后的 shell 输出文本。
    """
    lines = [
        f'exit_code={exit_code}',
        '[stdout]',
        stdout.rstrip(),
        '[stderr]',
        stderr.rstrip(),
    ]
    return '\n'.join(lines).strip()


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
        context (ToolExecutionContext): 当前调用上下文。
        raw_path (str): 原始路径字符串。
        must_exist (bool): 是否要求目标路径必须存在。
        expect_file (bool): 是否要求目标路径是文件。
        expect_dir (bool): 是否要求目标路径是目录。
    Returns:
        Path: 解析后的绝对路径。
    Raises:
        ToolExecutionError: 当路径越界、缺失或类型不符合预期时抛出。
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
        limit (int): 允许返回的最大字符数。
    Returns:
        str: 裁剪后的输出文本。
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
        path (Path): 待转换路径。
        root (Path): 工作区根目录。
    Returns:
        str: 相对路径字符串；不在工作区内时返回绝对路径字符串。
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    text = str(relative)
    return text if text else '.'


def _iter_output_chunks(text: str, chunk_size: int = 512) -> Iterator[str]:
    """把文本按固定块大小拆分为流式片段。

    Args:
        text (str): 原始文本。
        chunk_size (int): 每个片段的最大字符数。
    Returns:
        Iterator[str]: 顺序产出的文本片段迭代器。
    """
    if not text:
        return
    for start in range(0, len(text), chunk_size):
        yield text[start : start + chunk_size]


def _require_string(arguments: JSONDict, key: str) -> str:
    """读取必填字符串参数。

    Args:
        arguments (JSONDict): 工具调用参数。
        key (str): 目标字段名。
    Returns:
        str: 字段对应的字符串值。
    Raises:
        ToolExecutionError: 当字段缺失或不是字符串时抛出。
    """
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ToolExecutionError(f'Argument "{key}" must be a string')
    return value


def _get_string(arguments: JSONDict, key: str, *, default: str) -> str:
    """读取可选字符串参数，并在缺失时回退默认值。

    Args:
        arguments (JSONDict): 工具调用参数。
        key (str): 目标字段名。
        default (str): 默认值。
    Returns:
        str: 字段对应的字符串值。
    Raises:
        ToolExecutionError: 当字段存在但不是字符串时抛出。
    """
    value = arguments.get(key, default)
    if not isinstance(value, str):
        raise ToolExecutionError(f'Argument "{key}" must be a string')
    return value


def _get_bool(arguments: JSONDict, key: str, *, default: bool) -> bool:
    """读取可选布尔参数，并在缺失时回退默认值。

    Args:
        arguments (JSONDict): 工具调用参数。
        key (str): 目标字段名。
        default (bool): 默认值。
    Returns:
        bool: 字段对应的布尔值。
    Raises:
        ToolExecutionError: 当字段存在但不是布尔值时抛出。
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
        arguments (JSONDict): 工具调用参数。
        key (str): 目标字段名。
        default (int): 默认值。
        min_value (int | None): 可选最小值约束。
        max_value (int | None): 可选最大值约束。
    Returns:
        int: 字段对应的整数值。
    Raises:
        ToolExecutionError: 当字段不是整数或超出范围时抛出。
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
        arguments (JSONDict): 工具调用参数。
        key (str): 目标字段名。
        min_value (int | None): 可选最小值约束。
    Returns:
        int | None: 字段对应的整数值；缺失时返回 None。
    Raises:
        ToolExecutionError: 当字段存在但不是整数或小于最小值时抛出。
    """
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolExecutionError(f'Argument "{key}" must be an integer')
    if min_value is not None and value < min_value:
        raise ToolExecutionError(f'Argument "{key}" must be >= {min_value}')
    return value
