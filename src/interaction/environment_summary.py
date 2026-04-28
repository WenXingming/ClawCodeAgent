"""CLI 启动环境摘要模型模块。

本模块负责承载交互式 CLI 启动阶段的环境摘要数据，并提供稳定的单行文案格式化能力。
它只处理展示数据本身，不负责从 agent 或 runtime 中采集统计结果。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvironmentLoadSummary:
    """描述交互式启动阶段已发现的环境加载结果。

    外部通常先完成各 runtime 的计数采集，再通过 render_line() 生成适合直接
    输出到终端的一行摘要文本。该对象是只读展示模型，不依赖具体 agent 类型。
    """

    mcp_servers: int = 0  # int: 已配置或已发现的 MCP server 数量。
    plugins: int = 0  # int: 已成功发现的插件清单数量。
    hook_policies: int = 0  # int: 已加载并生效的 hook policy 数量。
    search_providers: int = 0  # int: 已发现并可用的搜索 provider 数量。
    load_errors: int = 0  # int: 启动阶段各 runtime 汇总的加载错误数量。

    def render_line(self) -> str:
        """渲染单行环境摘要文本。

        Args:
            None: 该方法直接读取当前摘要对象的字段。
        Returns:
            str: 适合直接输出到终端的一行环境摘要；若没有任何正向统计则返回空字符串。
        """
        parts = self._build_summary_parts()
        if not parts:
            return ''
        return f'Environment loaded: {", ".join(parts)}'

    def _build_summary_parts(self) -> tuple[str, ...]:
        """按固定顺序构建环境摘要片段。

        Args:
            None: 该方法直接读取当前摘要对象的字段。
        Returns:
            tuple[str, ...]: 已格式化好的摘要片段元组。
        """
        parts: list[str] = []
        self._append_count_part(parts, self.mcp_servers, singular='MCP server', plural='MCP servers')
        self._append_count_part(parts, self.plugins, singular='plugin', plural='plugins')
        self._append_count_part(parts, self.hook_policies, singular='hook policy', plural='hook policies')
        self._append_count_part(parts, self.search_providers, singular='search provider', plural='search providers')
        self._append_count_part(parts, self.load_errors, singular='load error', plural='load errors')
        return tuple(parts)

    @staticmethod
    def _append_count_part(parts: list[str], count: int, *, singular: str, plural: str) -> None:
        """把单个计数片段追加到摘要列表中。

        Args:
            parts (list[str]): 正在累积的摘要片段列表。
            count (int): 当前环境项的计数值。
            singular (str): 单数文案。
            plural (str): 复数文案。
        Returns:
            None: 该方法只原地修改片段列表。
        """
        if count <= 0:
            return
        noun = singular if count == 1 else plural
        parts.append(f'{count} {noun}')