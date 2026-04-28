"""定义 MCP 子模块共享的数据模型与传输层错误类型。

本模块只承载跨 manifest loader、runtime、renderer 和 transport 共享的值对象与异常类型，不直接负责 manifest 解析、远端请求或文本渲染，从而把协议数据结构与运行时逻辑清晰拆开。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core_contracts.protocol import JSONDict


@dataclass(frozen=True)
class MCPResource:
    """表示一次发现到的 MCP 资源定义。

    该对象既可表示工作区 manifest 中声明的本地资源，也可表示通过远端
    MCP server 动态列举出来的资源描述。
    """

    uri: str  # str: 资源的唯一 URI。
    server_name: str  # str: 资源所属的 MCP server 名称。
    source_path: Path | None = None  # Path | None: 定义该资源的 manifest 路径。
    name: str | None = None  # str | None: 面向人类显示的资源名称。
    description: str | None = None  # str | None: 资源用途说明。
    mime_type: str | None = None  # str | None: 资源内容的 MIME 类型。
    resolved_path: Path | None = None  # Path | None: 本地文件类资源解析后的绝对路径。
    inline_text: str | None = None  # str | None: manifest 内联携带的文本内容。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict: 保留额外协议元数据。

    def to_dict(self) -> JSONDict:
        """把资源对象转换为可序列化字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 适合继续向上游透传的资源字典。
        """
        payload: JSONDict = {
            'uri': self.uri,
            'server_name': self.server_name,
        }
        if self.name is not None:
            payload['name'] = self.name
        if self.description is not None:
            payload['description'] = self.description
        if self.mime_type is not None:
            payload['mime_type'] = self.mime_type
        if self.resolved_path is not None:
            payload['resolved_path'] = str(self.resolved_path)
        if self.inline_text is not None:
            payload['inline_text'] = self.inline_text
        if self.source_path is not None:
            payload['source_path'] = str(self.source_path)
        if self.metadata:
            payload['metadata'] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class MCPTool:
    """表示远端 MCP server 暴露的单个工具定义。

    该对象主要被 MCPToolAdapter 用来展开顶层工具 schema，也会被运行时
    用于定位具体 server 和输入参数约束。
    """

    name: str  # str: 远端工具原始名称。
    server_name: str  # str: 提供该工具的 MCP server 名称。
    source_path: Path | None = None  # Path | None: 工具来源 manifest 路径。
    description: str | None = None  # str | None: 工具用途说明。
    input_schema: JSONDict = field(default_factory=dict)  # JSONDict: 工具输入参数 JSON Schema。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict: 附加 transport 或来源信息。

    def to_dict(self) -> JSONDict:
        """把工具定义转换为可序列化字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 工具定义的字典表达。
        """
        payload: JSONDict = {
            'name': self.name,
            'server_name': self.server_name,
            'input_schema': dict(self.input_schema),
        }
        if self.description is not None:
            payload['description'] = self.description
        if self.source_path is not None:
            payload['source_path'] = str(self.source_path)
        if self.metadata:
            payload['metadata'] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class MCPServerProfile:
    """表示一个可供运行时连接的 MCP server 配置。

    该对象统一封装 stdio、streamable-http 与 sse 三类 transport 所需的
    连接参数，便于 transport 层按同一入口调度请求。
    """

    name: str  # str: server 的规范化名称。
    transport: str  # str: transport 类型，如 stdio、streamable-http 或 sse。
    command: str  # str: stdio 模式下启动 server 的命令。
    url: str | None = None  # str | None: HTTP/SSE 模式下的请求入口。
    args: tuple[str, ...] = ()  # tuple[str, ...]: 启动命令附带的参数列表。
    headers: dict[str, str] = field(default_factory=dict)  # dict[str, str]: HTTP transport 请求头。
    env: dict[str, str] = field(default_factory=dict)  # dict[str, str]: stdio 子进程环境变量覆盖。
    cwd: Path | None = None  # Path | None: 启动 stdio server 时的工作目录。
    description: str | None = None  # str | None: server 的人类可读说明。
    source_path: Path | None = None  # Path | None: 定义该 server 的 manifest 路径。
    metadata: JSONDict = field(default_factory=dict)  # JSONDict: 额外保留的配置元数据。

    def to_dict(self) -> JSONDict:
        """把 server 配置转换为可序列化字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: server 配置的字典表达。
        """
        payload: JSONDict = {
            'name': self.name,
            'transport': self.transport,
            'command': self.command,
            'args': list(self.args),
            'headers': dict(self.headers),
            'env': dict(self.env),
        }
        if self.url is not None:
            payload['url'] = self.url
        if self.cwd is not None:
            payload['cwd'] = str(self.cwd)
        if self.description is not None:
            payload['description'] = self.description
        if self.source_path is not None:
            payload['source_path'] = str(self.source_path)
        if self.metadata:
            payload['metadata'] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class MCPToolCallResult:
    """表示一次远端 MCP 工具调用的归一化结果。

    上层既可以直接读取 `content` 作为面向模型的文本结果，也可以通过
    `raw_result` 继续追踪底层协议返回，便于调试、测试断言和二次渲染。
    """

    server_name: str  # str: 实际执行该工具的 server 名称。
    tool_name: str  # str: 实际调用的远端工具名称。
    content: str  # str: 供模型阅读的文本化结果。
    is_error: bool  # bool: 远端工具是否声明本次结果为错误。
    raw_result: JSONDict = field(default_factory=dict)  # JSONDict: 原始 JSON-RPC result 负载。

    def to_dict(self) -> JSONDict:
        """把工具调用结果转换为可序列化字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 工具调用结果的字典表达。
        """
        return {
            'server_name': self.server_name,
            'tool_name': self.tool_name,
            'content': self.content,
            'is_error': self.is_error,
            'raw_result': dict(self.raw_result),
        }


@dataclass(frozen=True)
class MCPLoadError:
    """表示 manifest 发现或解析阶段采集到的错误。

    该对象只保留最小诊断信息，供运行时摘要、测试断言和上层错误展示直接消费。
    """

    source_path: Path  # Path: 发生错误的 manifest 文件路径。
    detail: str  # str: 面向诊断的错误描述。


class MCPTransportError(RuntimeError):
    """表示一次 MCP transport 请求失败。

    该异常会保留 server、method、stderr 与 exit_code 等诊断上下文，供上层
    在错误提示或测试断言中直接使用。
    """

    def __init__(
        self,
        *,
        server_name: str,
        method: str,
        detail: str,
        stderr: str = '',
        exit_code: int | None = None,
    ) -> None:
        """初始化 transport 错误对象。

        Args:
            server_name (str): 触发失败的 MCP server 名称。
            method (str): 失败发生时正在执行的 MCP 方法名。
            detail (str): 失败详情摘要。
            stderr (str): 子进程或远端返回的 stderr 文本。
            exit_code (int | None): stdio 子进程退出码。
        Returns:
            None: 该方法初始化异常对象状态并构造最终错误消息。
        """
        self.server_name = server_name  # str: 失败对应的 MCP server 名称。
        self.method = method  # str: 失败对应的 MCP 方法名。
        self.detail = detail  # str: 面向上层展示的错误详情。
        self.stderr = stderr  # str: 附带的 stderr 文本。
        self.exit_code = exit_code  # int | None: 子进程退出码，HTTP 模式下通常为空。

        message = f'MCP transport failure for server {server_name!r} during {method}: {detail}'
        if exit_code is not None:
            message += f' (exit_code={exit_code})'
        if stderr:
            message += f' stderr={stderr}'
        super().__init__(message)