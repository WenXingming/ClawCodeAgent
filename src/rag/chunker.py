"""文档滑动窗口切分器（RAG 模块内部实现）。

职责单一：接收原始文档，按滑动窗口策略产出 RagChunk 列表。
不依赖任何 I/O、模型调用或向量运算，可独立测试。
"""

from __future__ import annotations

from core_contracts.rag import RagChunk, RagDocument, RagIndexError


class DocumentChunker:
    """将单篇文档按滑动窗口策略切分为多个文本分块。"""

    _SENTENCE_BREAK_CHARS = frozenset({'.', '!', '?', '。', '！', '？', ';', '；', ':', '：'})

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
                end = self._find_break_point(content, start, end, chunk_size)
            stripped = content[start:end].strip()
            if stripped:
                segments.append(stripped)
            start += step
            if step <= 0:
                break

        return segments

    def _find_break_point(self, content: str, start: int, end: int, chunk_size: int) -> int:
        """在 [start, end] 窗口内寻找最靠近 end 的自然断点。

        断点优先级：
            1) 换行符
            2) 句末标点（中英文 . ! ? ; :）
            3) 任意空白符
            4) 当前截断命中词中间时，优先向前找到最近词边界（允许轻微超出 chunk_size）
            5) 回退到 end 强制截断

        Args:
            content (str): 被搜索的文本全文。
            start (int): 搜索窗口的起始位置（含）。
            end (int): 搜索窗口的终止位置（不含），也是找不到断点时的回退值。
            chunk_size (int): 当前分块上限字符数，用于控制向前探测的最大距离。
        Returns:
            int: 最佳断点位置（断点后第一个字符的索引），找不到时返回 end。
        """
        newline_pos = content.rfind('\n', start, end)
        if newline_pos > start:
            return newline_pos + 1

        sentence_break = self._find_sentence_break_point(content, start, end)
        if sentence_break is not None:
            return sentence_break

        whitespace_break = self._find_whitespace_break_point(content, start, end)
        if whitespace_break is not None:
            return whitespace_break

        if self._is_mid_word_cut(content, end):
            backward_word_break = self._find_backward_word_break_point(content, start, end)
            if backward_word_break is not None:
                return backward_word_break

            forward_probe_span = max(8, chunk_size // 2)
            forward_limit = min(len(content), end + forward_probe_span)
            forward_word_break = self._find_forward_word_break_point(content, end, forward_limit)
            if forward_word_break is not None:
                return forward_word_break

        return end

    def _find_sentence_break_point(self, content: str, start: int, end: int) -> int | None:
        """在窗口内自右向左查找句末标点断点。

        Args:
            content (str): 被搜索的文本全文。
            start (int): 搜索窗口起始索引（含）。
            end (int): 搜索窗口终止索引（不含）。
        Returns:
            int | None: 命中时返回断点位置（标点后一个字符），否则返回 None。
        """
        for idx in range(end - 1, start - 1, -1):
            if content[idx] in self._SENTENCE_BREAK_CHARS:
                return idx + 1
        return None

    def _find_whitespace_break_point(self, content: str, start: int, end: int) -> int | None:
        """在窗口内自右向左查找空白符断点。

        Args:
            content (str): 被搜索的文本全文。
            start (int): 搜索窗口起始索引（含）。
            end (int): 搜索窗口终止索引（不含）。
        Returns:
            int | None: 命中时返回断点位置（空白符后一个字符），否则返回 None。
        """
        for idx in range(end - 1, start - 1, -1):
            if content[idx].isspace():
                return idx + 1
        return None

    def _is_mid_word_cut(self, content: str, end: int) -> bool:
        """判断当前 end 是否正落在词中间。

        Args:
            content (str): 被检查的文本全文。
            end (int): 当前拟切分位置（不含）。
        Returns:
            bool: 若 end 左右字符都属于词字符则返回 True。
        """
        if end <= 0 or end >= len(content):
            return False
        return self._is_word_char(content[end - 1]) and self._is_word_char(content[end])

    def _find_backward_word_break_point(self, content: str, start: int, end: int) -> int | None:
        """在窗口内自右向左查找最近词边界。

        Args:
            content (str): 被搜索的文本全文。
            start (int): 搜索窗口起始索引（含）。
            end (int): 搜索窗口终止索引（不含）。
        Returns:
            int | None: 命中时返回边界后一个字符位置，否则返回 None。
        """
        for idx in range(end - 1, start - 1, -1):
            if not self._is_word_char(content[idx]):
                candidate = idx + 1
                if candidate > start:
                    return candidate
        return None

    def _find_forward_word_break_point(self, content: str, end: int, forward_limit: int) -> int | None:
        """在 end 之后有限探测窗口内查找最近词边界。

        Args:
            content (str): 被搜索的文本全文。
            end (int): 当前拟切分位置（不含）。
            forward_limit (int): 向前探测的上边界（不含）。
        Returns:
            int | None: 命中时返回边界后一个字符位置，否则返回 None。
        """
        for idx in range(end, forward_limit):
            if not self._is_word_char(content[idx]):
                return idx + 1
        return None

    def _is_word_char(self, char: str) -> bool:
        """判断单个字符是否属于词字符。

        Args:
            char (str): 待判定的单个字符。
        Returns:
            bool: 字母、数字或下划线返回 True，其它返回 False。
        """
        return char.isalnum() or char == '_'
