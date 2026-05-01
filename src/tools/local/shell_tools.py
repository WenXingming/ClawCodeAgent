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
from core_contracts.tools_contracts import ToolDescriptor
from tools.local.bash_security import ShellSecurityPolicy
from tools.executor import ToolExecutionContext, ToolExecutionError, ToolPermissionError, ToolStreamUpdate


@dataclass(frozen=True)
class ShellToolProvider:
    """Shell 工具提供者，兼具工具定义、权限校验与执行调度。

    通过构造函数注入安全策略，对外暴露 build_tool 构建 bash 工具描述符。
    内部私有方法 _run / _run_streaming 分别处理普通与流式执行。
    """

    shell_security_policy: ShellSecurityPolicy  # ShellSecurityPolicy: 注入的 shell 命令安全策略实例。

    # ── 公有方法 ──────────────────────────────────────────────────────────

    def build_tool(self) -> ToolDescriptor:
        """构建 bash 工具定义。

        Returns:
            带有 handler 和 stream_handler 的 bash 工具描述符。
        """
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
            handler=self._run,
            stream_handler=self._run_streaming,
        )

    # ── 私有方法（深度优先调用链顺序） ─────────────────────────────────────

    def _run(self, arguments: JSONDict, context: ToolExecutionContext) -> str | tuple[str, JSONDict]:
        """执行 shell 命令并返回结构化文本结果。"""
        command = self._require_string(arguments, 'command')
        self._ensure_shell_allowed(command, context)

        process = ShellProcess(command, context)
        stdout, stderr, exit_code = process.execute()

        rendered = self._render_output(stdout, stderr, exit_code)
        output = self._truncate_output(rendered, context.max_output_chars)

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

    def _run_streaming(
        self,
        arguments: JSONDict,
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamUpdate]:
        """执行 shell 命令并按 stdout/stderr 分块输出流式事件。"""
        command = self._require_string(arguments, 'command')
        self._ensure_shell_allowed(command, context)

        process = ShellProcess(command, context)
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        for stream_name, chunk in process.stream():
            if stream_name == 'stdout':
                stdout_chunks.append(chunk)
            else:
                stderr_chunks.append(chunk)
            yield ToolStreamUpdate(kind=stream_name, chunk=chunk)

        stdout = ''.join(stdout_chunks)
        stderr = ''.join(stderr_chunks)
        exit_code = process.returncode or 0

        rendered = self._render_output(stdout, stderr, exit_code)
        output = self._truncate_output(rendered, context.max_output_chars)
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

    def _require_string(self, arguments: JSONDict, key: str) -> str:
        """读取必填字符串参数。"""
        value = arguments.get(key)
        if not isinstance(value, str):
            raise ToolExecutionError(f'Argument "{key}" must be a string')
        return value

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

    @staticmethod
    def _render_output(stdout: str, stderr: str, exit_code: int) -> str:
        """把 shell 执行结果渲染成 transcript 友好的文本格式。"""
        lines = [
            f'exit_code={exit_code}',
            '[stdout]',
            stdout.rstrip(),
            '[stderr]',
            stderr.rstrip(),
        ]
        return '\n'.join(lines).strip()

    @staticmethod
    def _truncate_output(text: str, limit: int) -> str:
        """按上限裁剪输出，同时尽量保留头尾信息。"""
        if limit <= 0 or len(text) <= limit:
            return text

        half = max(1, limit // 2)
        head = text[:half]
        tail = text[-half:]
        return f'{head}\n...[output truncated, total {len(text)} chars]...\n{tail}'


class ShellProcess:
    """有状态的 shell 子进程生命周期封装。

    负责启动、流读取、终止全生命周期，每个实例拥有属于自己的子进程状态。
    """

    def __init__(self, command: str, context: ToolExecutionContext) -> None:
        self._command = command
        self._context = context
        self._process: subprocess.Popen[str] | None = None

    @property
    def returncode(self) -> int | None:
        """子进程退出码，仅在 execute() 返回后或 stream() 完成后有效。"""
        if self._process is None:
            return None
        return self._process.returncode

    # ── 公有方法 ──────────────────────────────────────────────────────────

    def execute(self) -> tuple[str, str, int]:
        """同步执行并返回 (stdout, stderr, exit_code)。

        Raises:
            ToolExecutionError: 命令执行超时时抛出。
        """
        self._start()
        try:
            stdout, stderr = self._process.communicate(timeout=self._context.command_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self.terminate()
            raise ToolExecutionError(
                f'Shell command timed out after {self._context.command_timeout_seconds} seconds'
            ) from exc

        return stdout or '', stderr or '', int(self._process.returncode)

    def stream(self) -> Iterator[tuple[str, str]]:
        """流式执行，逐块产出 (stream_name, chunk) 元组。

        内部管理双线程读取 stdout/stderr，自动处理超时与进程终止。
        迭代器耗尽时子进程已退出，可通过 returncode 属性获取退出码。

        Raises:
            ToolExecutionError: 命令执行超时时抛出。
        """
        self._start()
        start_time = time.monotonic()
        timeout = self._context.command_timeout_seconds
        updates: queue.Queue[tuple[str, str]] = queue.Queue()

        reader_threads = [
            threading.Thread(
                target=ShellProcess._drain_stream,
                args=(self._process.stdout, 'stdout', updates),
                daemon=True,
            ),
            threading.Thread(
                target=ShellProcess._drain_stream,
                args=(self._process.stderr, 'stderr', updates),
                daemon=True,
            ),
        ]
        for thread in reader_threads:
            thread.start()

        closed_streams = {'stdout': False, 'stderr': False}
        try:
            while True:
                if time.monotonic() - start_time > timeout:
                    self.terminate()
                    raise ToolExecutionError(
                        f'Shell command timed out after {timeout} seconds'
                    )

                try:
                    stream_name, chunk = updates.get(timeout=0.05)
                except queue.Empty:
                    if self._process.poll() is not None and all(closed_streams.values()):
                        break
                    continue

                if stream_name.endswith('_closed'):
                    closed_streams[stream_name[:-7]] = True
                    if self._process.poll() is not None and all(closed_streams.values()):
                        break
                    continue

                yield stream_name, chunk
        finally:
            for thread in reader_threads:
                thread.join(timeout=0.2)

        remaining_timeout = max(0.1, timeout - (time.monotonic() - start_time))
        try:
            self._process.wait(timeout=remaining_timeout)
        except subprocess.TimeoutExpired as exc:
            self.terminate()
            raise ToolExecutionError(
                f'Shell command timed out after {timeout} seconds'
            ) from exc

    def terminate(self) -> None:
        """终止子进程并尽量回收管道。"""
        if self._process is None:
            return
        self._process.kill()
        try:
            self._process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass

    # ── 私有方法 ──────────────────────────────────────────────────────────

    def _start(self) -> None:
        """按统一参数启动 shell 子进程。"""
        environment = dict(os.environ)
        environment.update(self._context.safe_env)
        self._process = subprocess.Popen(
            self._command,
            shell=True,
            cwd=self._context.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=environment,
            bufsize=1,
        )

    @staticmethod
    def _drain_stream(
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
