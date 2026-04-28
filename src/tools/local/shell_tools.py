"""本地 shell 工具集合。"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Iterator

from core_contracts.protocol import JSONDict, ToolExecutionResult
from tools.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutionContext, ToolExecutionError, ToolPermissionError, ToolStreamUpdate
from tools.registry import LocalTool


def build_shell_tool(shell_security_policy: ShellSecurityPolicy) -> LocalTool:
    """构建 bash 工具定义。"""
    handler = _ShellToolHandler(shell_security_policy)
    return LocalTool(
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

    shell_security_policy: ShellSecurityPolicy

    def run(self, arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
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

    def run_streaming(
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


def _execute_shell_command(command: str, context: ToolExecutionContext) -> tuple[str, str, int]:
    """执行 shell 命令并返回 stdout、stderr 与退出码。"""
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
    """把 shell 执行结果渲染成 transcript 友好的文本格式。"""
    lines = [
        f'exit_code={exit_code}',
        '[stdout]',
        stdout.rstrip(),
        '[stderr]',
        stderr.rstrip(),
    ]
    return '\n'.join(lines).strip()


def _truncate_output(text: str, limit: int) -> str:
    """按上限裁剪输出，同时尽量保留头尾信息。"""
    if limit <= 0 or len(text) <= limit:
        return text

    half = max(1, limit // 2)
    head = text[:half]
    tail = text[-half:]
    return f'{head}\n...[output truncated, total {len(text)} chars]...\n{tail}'


def _require_string(arguments: JSONDict, key: str) -> str:
    """读取必填字符串参数。"""
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ToolExecutionError(f'Argument "{key}" must be a string')
    return value