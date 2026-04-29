"""本地 shell 工具集合。

提供 bash 工具，支持受权限和安全策略约束的 shell 命令执行，
包含普通执行与流式输出两种模式。
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Iterator

from core_contracts.messaging import ToolExecutionResult
from core_contracts.primitives import JSONDict
from core_contracts.tools import ToolDescriptor
from tools.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutionContext, ToolExecutionError, ToolPermissionError, ToolStreamUpdate


def build_shell_tool(shell_security_policy: ShellSecurityPolicy) -> ToolDescriptor:
    """构建 bash 工具定义。
    Args:
        shell_security_policy (ShellSecurityPolicy): 控制命令风险判断的安全策略。
    Returns:
        ToolDescriptor: 带有 handler 和 stream_handler 的 bash 工具描述符。
    """
    handler = _ShellToolHandler(shell_security_policy)
    return ToolDescriptor(
        name='bash',
        description='在当前工作区执行 shell 命令（受权限和安全策略约束）。',
        parameters={
            'type': 'object',
            'properties': {
                'command': {'type': 'string'},
            },
            'required': ['command'],
        },
        handler=handler.run,
        stream_handler=handler.run_streaming,
    )


@dataclass(frozen=True)
class _ShellToolHandler:
    """封装 bash 工具的普通与流式执行逻辑。"""

    shell_security_policy: ShellSecurityPolicy  # ShellSecurityPolicy: 安全策略实例。

    def run(self, arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
        """执行 shell 命令并返回结构化文本结果。
        Args:
            arguments (JSONDict): 包含 command 的工具参数。
            context (ToolExecutionContext): 工具执行上下文。
        Returns:
            str | tuple[str, JSONDict]: 命令输出及执行元数据。
        Raises:
            ToolPermissionError: 当 shell 权限或安全策略拒绝命令时抛出。
            ToolExecutionError: 当命令超时或执行失败时抛出。
        """
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

    def run_streaming(
        self,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """执行 shell 命令并按 stdout/stderr 分块输出流式事件。
        Args:
            arguments (JSONDict): 包含 command 的工具参数。
            context (ToolExecutionContext): 工具执行上下文。
        Returns:
            Iterator[ToolStreamUpdate]: stdout/stderr 分块与最终 result 事件的序列。
        Raises:
            ToolPermissionError: 当 shell 权限或安全策略拒绝命令时抛出。
            ToolExecutionError: 当命令超时时抛出。
        """
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
        """检查 shell 权限和命令安全策略。
        Args:
            command (str): 待检查的命令文本。
            context (ToolExecutionContext): 工具执行上下文。
        Returns:
            None: 权限检查通过时无返回值。
        Raises:
            ToolPermissionError: 当 shell 权限未开启或安全策略拒绝命令时抛出。
        """
        allowed, reason = self.shell_security_policy.check_shell_security(
            command,
            allow_shell=context.permissions.allow_shell_commands,
            allow_destructive=context.permissions.allow_destructive_shell_commands,
        )
        if allowed:
            return
        raise ToolPermissionError(f'Shell command blocked: {reason}')


def _execute_shell_command(command: str, context: ToolExecutionContext) -> tuple[str, str, int]:
    """执行 shell 命令并返回 stdout、stderr 与退出码。
    Args:
        command (str): 待执行的命令文本。
        context (ToolExecutionContext): 工具执行上下文。
    Returns:
        tuple[str, str, int]: (stdout, stderr, exit_code) 元组。
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
    """按统一参数启动一个 shell 子进程。
    Args:
        command (str): 待执行的命令文本。
        context (ToolExecutionContext): 工具执行上下文。
    Returns:
        subprocess.Popen[str]: 已启动的子进程对象。
    """
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
    """终止子进程并尽量回收管道。
    Args:
        process (subprocess.Popen[str]): 待终止的子进程对象。
    Returns:
        None: 无返回值。
    """
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
    """把子进程 stdout 或 stderr 按行推入线程安全队列。
    Args:
        stream: 子进程的 stdout 或 stderr 流对象。
        stream_name (str): 流标识，如 'stdout' 或 'stderr'。
        updates (queue.Queue): 线程安全的更新队列。
    Returns:
        None: 读取完毕后通过队列推送 _closed 标记。
    """
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
        str: 包含 exit_code、stdout、stderr 标签的结构化文本。
    """
    lines = [
        f'exit_code={exit_code}',
        '[stdout]',
        stdout.rstrip(),
        '[stderr]',
        stderr.rstrip(),
    ]
    return '\n'.join(lines).strip()


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
