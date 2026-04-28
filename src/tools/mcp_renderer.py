"""负责 MCP 资源与工具结果的纯文本渲染。

本模块把 resources/read 和 tools/call 的协议负载转换成适合模型直接阅读的稳定文本，避免运行时层混入协议细节、序列化分支和截断格式选择。
"""

from __future__ import annotations

import json
from typing import Any

from .mcp_models import MCPResource


class MCPRenderer:
    """负责把 MCP 返回负载渲染成稳定的文本格式。

    运行时通常通过该类完成资源过滤、文本裁剪以及协议 content 数组的可读化，
    从而把上层逻辑保持在“选择资源/工具”这一层，而不需要感知底层 JSON 结构。
    """

    @staticmethod
    def render_resource_contents(contents: Any) -> str:
        """把 resources/read 返回的 contents 数组渲染成文本。

        Args:
            contents (Any): MCP resources/read 返回的 contents 字段。
        Returns:
            str: 拼接后的可读文本；无法识别或内容为空时返回空字符串。
        """
        if not isinstance(contents, list):
            return ''

        rendered: list[str] = []
        for item in contents:
            if not isinstance(item, dict):
                continue
            text = item.get('text')
            if isinstance(text, str):
                rendered.append(text)
                continue
            blob = item.get('blob')
            if isinstance(blob, str):
                mime_type = item.get('mimeType') if isinstance(item.get('mimeType'), str) else 'application/octet-stream'
                rendered.append(f'[blob:{mime_type}] {blob}')
                continue
            rendered.append(json.dumps(item, ensure_ascii=True, indent=2))
        return '\n\n'.join(part for part in rendered if part).strip()

    @staticmethod
    def render_tool_call_result(result: dict[str, Any]) -> str:
        """把 tools/call 的 result 负载渲染为文本。

        Args:
            result (dict[str, Any]): MCP tools/call 返回的 result 字典。
        Returns:
            str: 供模型消费的文本结果；当 content 不是数组时回退为 JSON 文本。
        """
        content = result.get('content')
        if not isinstance(content, list):
            return json.dumps(result, ensure_ascii=True, indent=2)

        rendered: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get('text')
            if isinstance(text, str):
                rendered.append(text)
                continue
            rendered.append(json.dumps(item, ensure_ascii=True, indent=2))
        return '\n\n'.join(part for part in rendered if part).strip()

    @staticmethod
    def filter_resources(resources: tuple[MCPResource, ...], *, query: str | None = None) -> tuple[MCPResource, ...]:
        """按查询词过滤资源列表。

        Args:
            resources (tuple[MCPResource, ...]): 待过滤的资源序列。
            query (str | None): 可选查询词，匹配 URI、server、名称或描述。
        Returns:
            tuple[MCPResource, ...]: 过滤后的资源元组；未提供查询词时返回原序列。
        """
        if not query:
            return resources
        needle = query.lower()
        return tuple(
            resource
            for resource in resources
            if needle in resource.uri.lower()
            or needle in resource.server_name.lower()
            or needle in (resource.name or '').lower()
            or needle in (resource.description or '').lower()
        )

    @staticmethod
    def truncate(value: str, max_chars: int) -> str:
        """按上限裁剪文本内容。

        Args:
            value (str): 原始文本。
            max_chars (int): 最大允许字符数。
        Returns:
            str: 原文或追加省略号后的裁剪结果。
        """
        if max_chars <= 0 or len(value) <= max_chars:
            return value
        return value[:max_chars] + '...'