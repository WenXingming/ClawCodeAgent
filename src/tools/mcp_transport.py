"""负责 MCP transport 请求的分发、执行与错误归一化。

本模块把一次 MCP 方法调用分发到 stdio 或 HTTP/SSE transport，并在单次请求内部完成 initialize、notifications/initialized、业务方法调用、响应解析与错误封装，是运行时访问远端 server 的最低层执行入口。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .mcp_models import MCPServerProfile, MCPTransportError


MCP_PROTOCOL_VERSION = '2025-11-25'  # str: 当前请求默认使用的 MCP 协议版本。
_DEFAULT_TIMEOUT_SECONDS = 10.0  # float: 单次 MCP 请求的默认超时时间，单位秒。


@dataclass(frozen=True)
class MCPTransportClient:
    """负责对单个 MCP server 发起一次性请求。

    外部只需要调用 request。类内部会根据 server.transport 选择 stdio 或
    HTTP/SSE 实现，并在同一条调用链上完成请求编码、响应解码和错误封装。
    """

    default_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS  # float: 默认请求超时时间，单位秒。

    def request(
        self,
        server: MCPServerProfile,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """按 server transport 分发一次 MCP 请求。

        Args:
            server (MCPServerProfile): 目标 MCP server 配置。
            method (str): 要调用的 MCP 方法名。
            params (dict[str, Any]): 方法参数对象。
            timeout_seconds (float | None): 可选超时时间；为空时使用默认值。
        Returns:
            dict[str, Any]: JSON-RPC result 中的字典负载。
        Raises:
            MCPTransportError: 当 transport 不支持或请求失败时抛出。
        """
        effective_timeout = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if server.transport == 'stdio':
            return self._request_stdio(server, method, params, timeout_seconds=effective_timeout)
        if server.transport in {'streamable-http', 'sse'}:
            return self._request_http(server, method, params, timeout_seconds=effective_timeout)
        raise MCPTransportError(
            server_name=server.name,
            method=method,
            detail=f'Unsupported MCP transport: {server.transport}',
        )

    def _request_stdio(
        self,
        server: MCPServerProfile,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """按可用编码模式尝试 stdio 请求。

        Args:
            server (MCPServerProfile): 目标 MCP server 配置。
            method (str): 要调用的 MCP 方法名。
            params (dict[str, Any]): 方法参数对象。
            timeout_seconds (float): 请求超时时间，单位秒。
        Returns:
            dict[str, Any]: JSON-RPC result 中的字典负载。
        Raises:
            MCPTransportError: 当 framed 与 jsonl 两种模式均失败时抛出。
        """
        last_error: MCPTransportError | None = None
        for transport_mode in ('framed', 'jsonl'):
            try:
                return self._request_stdio_with_mode(
                    server,
                    method,
                    params,
                    timeout_seconds=timeout_seconds,
                    transport_mode=transport_mode,
                )
            except MCPTransportError as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise MCPTransportError(server_name=server.name, method=method, detail='Unknown stdio transport failure')

    def _request_stdio_with_mode(
        self,
        server: MCPServerProfile,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
        transport_mode: str,
    ) -> dict[str, Any]:
        """按指定 stdio 编码模式执行请求。

        Args:
            server (MCPServerProfile): 目标 MCP server 配置。
            method (str): 要调用的 MCP 方法名。
            params (dict[str, Any]): 方法参数对象。
            timeout_seconds (float): 请求超时时间，单位秒。
            transport_mode (str): 当前尝试的 stdio 编码模式。
        Returns:
            dict[str, Any]: JSON-RPC result 中的字典负载。
        Raises:
            MCPTransportError: 当 transport_mode 不受支持或请求失败时抛出。
        """
        if transport_mode == 'framed':
            return self._request_stdio_framed(server, method, params, timeout_seconds=timeout_seconds)
        if transport_mode == 'jsonl':
            return self._request_stdio_jsonl(server, method, params, timeout_seconds=timeout_seconds)
        raise MCPTransportError(
            server_name=server.name,
            method=method,
            detail=f'Unsupported stdio transport mode: {transport_mode}',
        )

    def _request_stdio_framed(
        self,
        server: MCPServerProfile,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """使用 Content-Length framing 协议执行 stdio 请求。

        Args:
            server (MCPServerProfile): 目标 MCP server 配置。
            method (str): 要调用的 MCP 方法名。
            params (dict[str, Any]): 方法参数对象。
            timeout_seconds (float): 请求超时时间，单位秒。
        Returns:
            dict[str, Any]: JSON-RPC result 中的字典负载。
        Raises:
            MCPTransportError: 当子进程启动、超时、初始化或方法响应失败时抛出。
        """
        command = [server.command, *server.args]
        env = os.environ.copy()
        env.update(server.env)
        payload = b''.join(
            [
                self._encode_mcp_message(
                    {
                        'jsonrpc': '2.0',
                        'id': 1,
                        'method': 'initialize',
                        'params': {
                            'protocolVersion': MCP_PROTOCOL_VERSION,
                            'capabilities': {},
                            'clientInfo': {
                                'name': 'claw-code-agent',
                                'version': '0.1.0',
                            },
                        },
                    }
                ),
                self._encode_mcp_message(
                    {
                        'jsonrpc': '2.0',
                        'method': 'notifications/initialized',
                        'params': {},
                    }
                ),
                self._encode_mcp_message(
                    {
                        'jsonrpc': '2.0',
                        'id': 2,
                        'method': method,
                        'params': params,
                    }
                ),
            ]
        )

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(server.cwd) if server.cwd is not None else None,
                env=env,
            )
        except OSError as exc:
            raise MCPTransportError(
                server_name=server.name,
                method=method,
                detail=f'Failed to spawn MCP server: {exc}',
            ) from exc

        try:
            stdout_data, stderr_data = process.communicate(input=payload, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            stdout_data, stderr_data = process.communicate()
            raise MCPTransportError(
                server_name=server.name,
                method=method,
                detail='Timed out waiting for MCP response',
                stderr=self._decode_stderr(stderr_data),
                exit_code=process.returncode,
            ) from exc

        stderr_text = self._decode_stderr(stderr_data)
        responses = self._decode_mcp_messages(stdout_data)
        initialize_response = self._find_response(responses, 1)
        if initialize_response is None:
            raise MCPTransportError(
                server_name=server.name,
                method=method,
                detail='Missing initialize response',
                stderr=stderr_text,
                exit_code=process.returncode,
            )

        initialize_error = initialize_response.get('error') if isinstance(initialize_response, dict) else None
        if isinstance(initialize_error, dict):
            raise MCPTransportError(
                server_name=server.name,
                method='initialize',
                detail=str(initialize_error.get('message') or initialize_error),
                stderr=stderr_text,
                exit_code=process.returncode,
            )

        response = self._find_response(responses, 2)
        if response is None:
            raise MCPTransportError(
                server_name=server.name,
                method=method,
                detail='Missing method response',
                stderr=stderr_text,
                exit_code=process.returncode,
            )

        response_error = response.get('error') if isinstance(response, dict) else None
        if isinstance(response_error, dict):
            raise MCPTransportError(
                server_name=server.name,
                method=method,
                detail=str(response_error.get('message') or response_error),
                stderr=stderr_text,
                exit_code=process.returncode,
            )

        result = response.get('result') if isinstance(response, dict) else None
        if not isinstance(result, dict):
            return {}
        return result

    @staticmethod
    def _encode_mcp_message(payload: dict[str, Any]) -> bytes:
        """把 JSON-RPC 负载编码为 MCP framed 消息。

        Args:
            payload (dict[str, Any]): 待发送的 JSON-RPC 消息对象。
        Returns:
            bytes: 带 Content-Length 头部的二进制消息。
        """
        body = json.dumps(payload, ensure_ascii=True).encode('utf-8')
        header = f'Content-Length: {len(body)}\r\n\r\n'.encode('ascii')
        return header + body

    @staticmethod
    def _decode_stderr(raw: bytes | None) -> str:
        """把 stderr 字节串解码为文本。

        Args:
            raw (bytes | None): 原始 stderr 字节串。
        Returns:
            str: 去除首尾空白后的 stderr 文本。
        """
        if not raw:
            return ''
        return raw.decode('utf-8', errors='replace').strip()

    @staticmethod
    def _decode_mcp_messages(raw: bytes | None) -> tuple[dict[str, Any], ...]:
        """把 framed stdio 输出解析为 JSON-RPC 消息序列。

        Args:
            raw (bytes | None): 原始 stdout 字节串。
        Returns:
            tuple[dict[str, Any], ...]: 解析得到的 JSON-RPC 消息元组。
        """
        if not raw:
            return ()

        messages: list[dict[str, Any]] = []
        cursor = 0
        while cursor < len(raw):
            header_end = raw.find(b'\r\n\r\n', cursor)
            if header_end == -1:
                break
            header_blob = raw[cursor:header_end].decode('ascii', errors='replace')
            cursor = header_end + 4
            content_length = MCPTransportClient._parse_content_length(header_blob)
            if content_length <= 0:
                break
            body = raw[cursor:cursor + content_length]
            if len(body) < content_length:
                break
            cursor += content_length
            try:
                payload = json.loads(body.decode('utf-8', errors='replace'))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                messages.append(payload)
        return tuple(messages)

    @staticmethod
    def _parse_content_length(header_blob: str) -> int:
        """从 MCP framed 头部解析 Content-Length。

        Args:
            header_blob (str): 头部文本。
        Returns:
            int: 解析出的内容长度；非法时返回 0。
        """
        for raw_line in header_blob.split('\r\n'):
            name, _, value = raw_line.partition(':')
            if name.lower() != 'content-length':
                continue
            try:
                return int(value.strip())
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _find_response(messages: tuple[dict[str, Any], ...], request_id: int) -> dict[str, Any] | None:
        """在消息序列中定位指定 request id 的响应。

        Args:
            messages (tuple[dict[str, Any], ...]): 待检索的消息序列。
            request_id (int): 目标请求 id。
        Returns:
            dict[str, Any] | None: 匹配到的响应对象；不存在时返回 None。
        """
        for message in messages:
            if message.get('id') == request_id:
                return message
        return None

    def _request_stdio_jsonl(
        self,
        server: MCPServerProfile,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """使用 JSONL 兼容模式执行 stdio 请求。

        Args:
            server (MCPServerProfile): 目标 MCP server 配置。
            method (str): 要调用的 MCP 方法名。
            params (dict[str, Any]): 方法参数对象。
            timeout_seconds (float): 请求超时时间，单位秒。
        Returns:
            dict[str, Any]: JSON-RPC result 中的字典负载。
        Raises:
            MCPTransportError: 当子进程启动、超时、初始化或方法响应失败时抛出。
        """
        command = [server.command, *server.args]
        env = os.environ.copy()
        env.update(server.env)

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(server.cwd) if server.cwd is not None else None,
                env=env,
            )
        except OSError as exc:
            raise MCPTransportError(
                server_name=server.name,
                method=method,
                detail=f'Failed to spawn MCP server: {exc}',
            ) from exc

        try:
            self._write_jsonl_message(
                process,
                {
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': 'initialize',
                    'params': {
                        'protocolVersion': MCP_PROTOCOL_VERSION,
                        'capabilities': {},
                        'clientInfo': {
                            'name': 'claw-code-agent',
                            'version': '0.1.0',
                        },
                    },
                },
                server_name=server.name,
                method=method,
            )
            initialize_response = self._read_jsonl_response_for_id(
                process,
                request_id=1,
                timeout_seconds=timeout_seconds,
                server_name=server.name,
                method=method,
            )

            initialize_error = initialize_response.get('error') if isinstance(initialize_response, dict) else None
            if isinstance(initialize_error, dict):
                raise MCPTransportError(
                    server_name=server.name,
                    method='initialize',
                    detail=str(initialize_error.get('message') or initialize_error),
                    stderr=self._collect_stderr(process),
                    exit_code=process.returncode,
                )

            self._write_jsonl_message(
                process,
                {
                    'jsonrpc': '2.0',
                    'method': 'notifications/initialized',
                    'params': {},
                },
                server_name=server.name,
                method=method,
            )
            self._write_jsonl_message(
                process,
                {
                    'jsonrpc': '2.0',
                    'id': 2,
                    'method': method,
                    'params': params,
                },
                server_name=server.name,
                method=method,
            )
            response = self._read_jsonl_response_for_id(
                process,
                request_id=2,
                timeout_seconds=timeout_seconds,
                server_name=server.name,
                method=method,
            )
        finally:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                process.terminate()

        response_error = response.get('error') if isinstance(response, dict) else None
        if isinstance(response_error, dict):
            raise MCPTransportError(
                server_name=server.name,
                method=method,
                detail=str(response_error.get('message') or response_error),
                stderr=self._collect_stderr(process),
                exit_code=process.returncode,
            )

        result = response.get('result') if isinstance(response, dict) else None
        if not isinstance(result, dict):
            return {}
        return result

    @staticmethod
    def _write_jsonl_message(
        process: subprocess.Popen[bytes],
        payload: dict[str, Any],
        *,
        server_name: str,
        method: str,
    ) -> None:
        """向 JSONL stdio 子进程写入一条请求。

        Args:
            process (subprocess.Popen[bytes]): 已启动的子进程对象。
            payload (dict[str, Any]): 待写入的 JSON-RPC 消息对象。
            server_name (str): 当前 server 名称，用于错误提示。
            method (str): 当前调用的方法名，用于错误提示。
        Returns:
            None: 无返回值。
        Raises:
            MCPTransportError: 当 stdin 管道不存在时抛出。
        """
        if process.stdin is None:
            raise MCPTransportError(server_name=server_name, method=method, detail='Missing stdio stdin pipe')
        body = json.dumps(payload, ensure_ascii=True).encode('utf-8') + b'\n'
        process.stdin.write(body)
        process.stdin.flush()

    def _read_jsonl_response_for_id(
        self,
        process: subprocess.Popen[bytes],
        *,
        request_id: int,
        timeout_seconds: float,
        server_name: str,
        method: str,
    ) -> dict[str, Any]:
        """从 JSONL stdio 输出中读取指定 id 的响应。

        Args:
            process (subprocess.Popen[bytes]): 已启动的子进程对象。
            request_id (int): 等待的响应 id。
            timeout_seconds (float): 请求超时时间，单位秒。
            server_name (str): 当前 server 名称，用于错误提示。
            method (str): 当前调用的方法名，用于错误提示。
        Returns:
            dict[str, Any]: 匹配到的 JSON-RPC 响应对象。
        Raises:
            MCPTransportError: 当 stdout 缺失、超时或进程提前关闭时抛出。
        """
        if process.stdout is None:
            raise MCPTransportError(server_name=server_name, method=method, detail='Missing stdio stdout pipe')

        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTransportError(
                    server_name=server_name,
                    method=method,
                    detail='Timed out waiting for MCP response',
                    stderr=self._collect_stderr(process),
                    exit_code=process.returncode,
                )

            raw_line = self._readline_with_timeout(process.stdout, remaining)
            if raw_line is None:
                raise MCPTransportError(
                    server_name=server_name,
                    method=method,
                    detail='Timed out waiting for MCP response',
                    stderr=self._collect_stderr(process),
                    exit_code=process.returncode,
                )
            if not raw_line:
                raise MCPTransportError(
                    server_name=server_name,
                    method=method,
                    detail='MCP server closed stdout before response',
                    stderr=self._collect_stderr(process),
                    exit_code=process.returncode,
                )

            line = raw_line.decode('utf-8', errors='replace').strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get('id') == request_id:
                return payload

    @staticmethod
    def _readline_with_timeout(stream: Any, timeout_seconds: float) -> bytes | None:
        """在超时限制内从流对象读取一行。

        Args:
            stream (Any): 具备 readline 方法的流对象。
            timeout_seconds (float): 超时时间，单位秒。
        Returns:
            bytes | None: 读取到的字节串；超时时返回 None。
        Raises:
            BaseException: 当底层读取线程抛出异常时原样透传。
        """
        holder: dict[str, bytes | BaseException] = {}

        def _worker() -> None:
            try:
                holder['line'] = stream.readline()
            except BaseException as exc:  # noqa: BLE001
                holder['error'] = exc

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout_seconds)
        if thread.is_alive():
            return None

        error = holder.get('error')
        if isinstance(error, BaseException):
            raise error

        line = holder.get('line')
        if isinstance(line, bytes):
            return line
        return b''

    @staticmethod
    def _collect_stderr(process: subprocess.Popen[bytes]) -> str:
        """收集子进程当前可读取的 stderr 文本。

        Args:
            process (subprocess.Popen[bytes]): 已启动的子进程对象。
        Returns:
            str: 解码后的 stderr 文本。
        """
        try:
            _stdout_data, stderr_data = process.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout_data, stderr_data = process.communicate()
        return MCPTransportClient._decode_stderr(stderr_data)

    def _request_http(
        self,
        server: MCPServerProfile,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """使用 HTTP 或 SSE transport 执行请求。

        Args:
            server (MCPServerProfile): 目标 MCP server 配置。
            method (str): 要调用的 MCP 方法名。
            params (dict[str, Any]): 方法参数对象。
            timeout_seconds (float): 请求超时时间，单位秒。
        Returns:
            dict[str, Any]: JSON-RPC result 中的字典负载。
        Raises:
            MCPTransportError: 当 URL 缺失、初始化失败或方法响应失败时抛出。
        """
        if not server.url:
            raise MCPTransportError(server_name=server.name, method=method, detail='Missing MCP server url')

        initialize_response = self._post_http_jsonrpc(
            server,
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': MCP_PROTOCOL_VERSION,
                    'capabilities': {},
                    'clientInfo': {
                        'name': 'claw-code-agent',
                        'version': '0.1.0',
                    },
                },
            },
            timeout_seconds=timeout_seconds,
            method_name='initialize',
        )
        init_error = initialize_response.get('error') if isinstance(initialize_response, dict) else None
        if isinstance(init_error, dict):
            raise MCPTransportError(
                server_name=server.name,
                method='initialize',
                detail=str(init_error.get('message') or init_error),
            )

        self._post_http_jsonrpc(
            server,
            {
                'jsonrpc': '2.0',
                'method': 'notifications/initialized',
                'params': {},
            },
            timeout_seconds=timeout_seconds,
            method_name='notifications/initialized',
            require_response=False,
        )

        response = self._post_http_jsonrpc(
            server,
            {
                'jsonrpc': '2.0',
                'id': 2,
                'method': method,
                'params': params,
            },
            timeout_seconds=timeout_seconds,
            method_name=method,
        )

        response_error = response.get('error') if isinstance(response, dict) else None
        if isinstance(response_error, dict):
            raise MCPTransportError(
                server_name=server.name,
                method=method,
                detail=str(response_error.get('message') or response_error),
            )
        result = response.get('result') if isinstance(response, dict) else None
        if not isinstance(result, dict):
            return {}
        return result

    def _post_http_jsonrpc(
        self,
        server: MCPServerProfile,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        method_name: str,
        require_response: bool = True,
    ) -> dict[str, Any]:
        """向 HTTP MCP endpoint 发送单个 JSON-RPC POST 请求。

        Args:
            server (MCPServerProfile): 目标 MCP server 配置。
            payload (dict[str, Any]): 待发送的 JSON-RPC 消息对象。
            timeout_seconds (float): 请求超时时间，单位秒。
            method_name (str): 当前调用的方法名，用于错误提示。
            require_response (bool): 是否要求必须存在响应体。
        Returns:
            dict[str, Any]: 匹配到的 JSON-RPC 响应对象；无需响应时返回空字典。
        Raises:
            MCPTransportError: 当 URL 缺失、HTTP 请求失败或响应缺失时抛出。
        """
        if not server.url:
            raise MCPTransportError(server_name=server.name, method=method_name, detail='Missing MCP server url')

        body = json.dumps(payload, ensure_ascii=True).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
        }
        headers.update(server.headers)

        request = urllib.request.Request(
            url=server.url,
            data=body,
            headers=headers,
            method='POST',
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
                messages = self._decode_http_mcp_messages(raw)
        except urllib.error.HTTPError as exc:
            detail = f'HTTP {exc.code}'
            try:
                error_body = exc.read().decode('utf-8', errors='replace').strip()
            except Exception:  # noqa: BLE001
                error_body = ''
            if error_body:
                detail = f'{detail}: {error_body[:500]}'
            raise MCPTransportError(server_name=server.name, method=method_name, detail=detail) from exc
        except urllib.error.URLError as exc:
            raise MCPTransportError(server_name=server.name, method=method_name, detail=f'HTTP transport error: {exc}') from exc

        if not require_response:
            return {}
        request_id = payload.get('id')
        if not isinstance(request_id, int):
            return messages[0] if messages else {}

        response_payload = self._find_response(messages, request_id)
        if response_payload is None:
            raise MCPTransportError(
                server_name=server.name,
                method=method_name,
                detail='Missing HTTP MCP response',
            )
        return response_payload

    @staticmethod
    def _decode_http_mcp_messages(raw: bytes | None) -> tuple[dict[str, Any], ...]:
        """解析 HTTP 返回中的 JSON 或 SSE data 消息。

        Args:
            raw (bytes | None): 原始 HTTP 响应体。
        Returns:
            tuple[dict[str, Any], ...]: 解析得到的 JSON-RPC 消息元组。
        """
        if not raw:
            return ()
        text = raw.decode('utf-8', errors='replace').strip()
        if not text:
            return ()

        parsed_direct = MCPTransportClient._parse_json_message(text)
        if parsed_direct is not None:
            return (parsed_direct,)

        messages: list[dict[str, Any]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('data:'):
                stripped = stripped[5:].strip()
            if stripped in {'[DONE]', 'done'}:
                continue
            parsed = MCPTransportClient._parse_json_message(stripped)
            if parsed is not None:
                messages.append(parsed)
        return tuple(messages)

    @staticmethod
    def _parse_json_message(raw_text: str) -> dict[str, Any] | None:
        """尝试把一段文本解析为单个 JSON 对象消息。

        Args:
            raw_text (str): 待解析的文本。
        Returns:
            dict[str, Any] | None: 解析成功时返回字典；否则返回 None。
        """
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None