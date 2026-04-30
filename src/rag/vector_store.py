"""基于余弦相似度的纯 Python 内存向量存储（RAG 模块内部实现）。

职责单一：管理命名集合的分块与嵌入向量，提供写入与线性检索能力。
不依赖任何 I/O 或模型调用，可独立测试。
"""

from __future__ import annotations

import math

from core_contracts.rag import RagChunk, RagCollectionNotFoundError, RagIndexError


class VectorStore:
    """基于余弦相似度的纯 Python 内存向量存储。"""

    def __init__(self) -> None:
        """初始化空向量存储，不预分配任何集合。"""
        self._store: dict[str, tuple[list[RagChunk], list[list[float]]]] = {}

    def upsert(
        self,
        name: str,
        chunks: list[RagChunk],
        vectors: list[list[float]],
    ) -> None:
        """将分块及其嵌入向量追加写入（或创建）指定集合。

        Args:
            name (str): 目标集合名称；不存在时自动创建。
            chunks (list[RagChunk]): 待写入的分块列表。
            vectors (list[list[float]]): 与 chunks 一一对应的嵌入向量列表。
        Raises:
            RagIndexError: chunks 与 vectors 数量不一致时抛出。
        """
        if len(chunks) != len(vectors):
            raise RagIndexError(
                f"分块数量 ({len(chunks)}) 与向量数量 ({len(vectors)}) 不一致。"
            )
        if name not in self._store:
            self._store[name] = ([], [])
        existing_chunks, existing_vectors = self._store[name]
        existing_chunks.extend(chunks)
        existing_vectors.extend(vectors)

    def search(
        self,
        name: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[tuple[RagChunk, float]]:
        """在指定集合中检索与查询向量最相似的分块（线性扫描）。

        Args:
            name (str): 目标集合名称，须已通过 upsert 创建。
            query_vector (list[float]): 查询嵌入向量。
            top_k (int): 返回相似度最高的分块数量上限。
        Returns:
            list[tuple[RagChunk, float]]: 按余弦相似度降序排列的分块与得分对列表。
        Raises:
            RagCollectionNotFoundError: 指定集合不存在时抛出。
        """
        if name not in self._store:
            raise RagCollectionNotFoundError(name)
        chunks, vectors = self._store[name]
        if not chunks:
            return []

        scored = [
            (chunk, self._cosine_similarity(query_vector, vec))
            for chunk, vec in zip(chunks, vectors)
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def drop(self, name: str) -> None:
        """删除指定集合及其全部数据。

        Args:
            name (str): 要删除的集合名称。
        Raises:
            RagCollectionNotFoundError: 集合不存在时抛出。
        """
        if name not in self._store:
            raise RagCollectionNotFoundError(name)
        del self._store[name]

    def list_names(self) -> list[str]:
        """返回当前存储中所有集合的名称列表。

        Returns:
            list[str]: 所有集合名称列表；尚未建立任何集合时返回空列表。
        """
        return list(self._store.keys())

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦相似度。

        Args:
            a (list[float]): 第一个嵌入向量。
            b (list[float]): 第二个嵌入向量，必须与 a 等长。
        Returns:
            float: 余弦相似度，范围 [0.0, 1.0]；任一向量为零向量时返回 0.0。
        """
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)
