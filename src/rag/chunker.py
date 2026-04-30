"""文档滑动窗口切分器（RAG 模块内部实现）。

职责单一：接收原始文档，按滑动窗口策略产出 RagChunk 列表。
不依赖任何 I/O、模型调用或向量运算，可独立测试。
"""

from __future__ import annotations

from core_contracts.rag import RagChunk, RagDocument, RagIndexError


class DocumentChunker:
    """将单篇文档按滑动窗口策略切分为多个文本分块。"""

    def __init__(self) -> None:
        """初始化无状态切分器。"""
        pass

    def chunk(
        self,
        document: RagDocument,
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[RagChunk]:
        """将一篇文档按滑动窗口策略切分为分块列表。

        Args:
            document (RagDocument): 待切分的原始文档契约对象。
            chunk_size (int): 每个分块的最大字符数（基于 Unicode 字符计数）。
            chunk_overlap (int): 相邻分块间的重叠字符数，须严格小于 chunk_size。
        Returns:
            list[RagChunk]: 切分完成的分块列表；文档为空时返回空列表。
        Raises:
            RagIndexError: chunk_size <= 0 或 chunk_overlap >= chunk_size 时抛出。
        """
        if chunk_size <= 0:
            raise RagIndexError(f"chunk_size 必须 >= 1，当前值: {chunk_size}")
        if chunk_overlap >= chunk_size:
            raise RagIndexError(
                f"chunk_overlap ({chunk_overlap}) 必须小于 chunk_size ({chunk_size})"
            )

        segments = self._split_text(document.content, chunk_size, chunk_overlap)
        return [
            RagChunk(
                chunk_id=f"{document.doc_id}#{position}",
                doc_id=document.doc_id,
                content=segment,
                position=position,
                metadata=dict(document.metadata),
            )
            for position, segment in enumerate(segments)
        ]

    def _split_text(self, content: str, chunk_size: int, chunk_overlap: int) -> list[str]:
        """使用滑动窗口策略将纯文本切分为字符串片段列表。

        Args:
            content (str): 待切分的文本全文。
            chunk_size (int): 每个片段的最大字符数。
            chunk_overlap (int): 相邻片段间重叠的字符数。
        Returns:
            list[str]: 切分后的文本片段列表；内容为空白时返回空列表。
        """
        if not content.strip():
            return []

        step = chunk_size - chunk_overlap
        segments: list[str] = []
        start = 0

        while start < len(content):
            end = min(start + chunk_size, len(content))
            if end < len(content):
                end = self._find_break_point(content, start, end)
            stripped = content[start:end].strip()
            if stripped:
                segments.append(stripped)
            start += step
            if step <= 0:
                break

        return segments

    def _find_break_point(self, content: str, start: int, end: int) -> int:
        """在 [start, end] 窗口内寻找最靠近 end 的自然断点（换行符 > 空格 > 强制截断）。

        Args:
            content (str): 被搜索的文本全文。
            start (int): 搜索窗口的起始位置（含）。
            end (int): 搜索窗口的终止位置（含），也是找不到断点时的回退值。
        Returns:
            int: 最佳断点位置（断点后第一个字符的索引），找不到时返回 end。
        """
        newline_pos = content.rfind('\n', start, end)
        if newline_pos > start:
            return newline_pos + 1
        space_pos = content.rfind(' ', start, end)
        if space_pos > start:
            return space_pos + 1
        return end
