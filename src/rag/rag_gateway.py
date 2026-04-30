"""RAG 模块唯一公开门面（Facade）。

本文件直接承担 RAG 流水线编排职责，不再引入独立 engine 包装层。
外部调用者只依赖：
    - RagGateway
    - core_contracts.rag_contracts 中的请求/结果契约与异常
"""

from __future__ import annotations

import time

from core_contracts.rag_contracts import (
    EmbeddingProvider,
    RagChunk,
    RagCollectionNotFoundError,
    RagDocument,
    RagError,
    RagIndexError,
    RagIndexRequest,
    RagIndexResult,
    RagQueryError,
    RagQueryRequest,
    RagQueryResult,
    RagRetrieveError,
    RagRetrieveRequest,
    RagRetrieveResult,
    RagRetrievedChunk,
)
from rag.answer_generator import AnswerGenerator
from rag.chunker import DocumentChunker
from rag.vector_store import VectorStore


class RagGateway:
    """RAG 模块门面与编排器。

    核心工作流：
      index_documents → 切分 → 嵌入 → 写入向量存储
      retrieve        → 嵌入查询 → 余弦相似度检索 → 返回分块
      query           → retrieve → 构建提示词 → 调用 ModelClient → 返回回答
    """

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        chunker: DocumentChunker,
        vector_store: VectorStore,
        answer_generator: AnswerGenerator,
    ) -> None:
        """初始化 RagGateway，注入全部流水线依赖。

        Args:
            embedding_provider (EmbeddingProvider): 文本嵌入提供者。
            chunker (DocumentChunker): 文档切分器。
            vector_store (VectorStore): 向量存储。
            answer_generator (AnswerGenerator): 回答生成器。
        """
        self._embedding_provider = embedding_provider
        self._chunker = chunker
        self._vector_store = vector_store
        self._answer_generator = answer_generator

    # ── 公有接口 ────────────────────────────────────────────────────────────

    def index_documents(self, request: RagIndexRequest) -> RagIndexResult:
        """索引一批文档：切分 → 嵌入 → 写入向量存储。

        Args:
            request (RagIndexRequest): 包含文档列表与索引参数的标准请求契约。
        Returns:
            RagIndexResult: 包含索引数量统计与耗时的结果契约。
        Raises:
            RagIndexError: 分块、嵌入或写入任一环节失败时抛出。
            ValueError: request.documents 为空时抛出。
        """
        if not request.documents:
            raise ValueError("RagIndexRequest.documents 不能为空，至少需要包含一篇文档。")
        t_start = time.monotonic()
        try:
            all_chunks = self._chunk_documents(
                documents=list(request.documents),
                chunk_size=request.chunk_size,
                chunk_overlap=request.chunk_overlap,
            )
            if all_chunks:
                vectors = self._embed_texts([chunk.content for chunk in all_chunks])
                self._vector_store.upsert(
                    name=request.collection_name,
                    chunks=all_chunks,
                    vectors=vectors,
                )
            return RagIndexResult(
                collection_name=request.collection_name,
                docs_indexed=len(request.documents),
                chunks_created=len(all_chunks),
                duration_s=time.monotonic() - t_start,
            )
        except RagIndexError:
            raise
        except RagError:
            raise
        except Exception as exc:
            raise RagIndexError(f"索引操作意外失败: {exc}") from exc

    def retrieve(self, request: RagRetrieveRequest) -> RagRetrieveResult:
        """在指定集合中执行纯向量相似度检索，不调用模型生成。

        Args:
            request (RagRetrieveRequest): 包含查询文本与检索参数的标准请求契约。
        Returns:
            RagRetrieveResult: 包含命中分块列表（按相似度降序）与耗时的结果契约。
        Raises:
            RagCollectionNotFoundError: 指定集合尚未通过 index_documents 建立时抛出。
            RagRetrieveError: 嵌入查询或向量检索失败时抛出。
        """
        if not request.query.strip():
            raise ValueError("RagRetrieveRequest.query 不能为空字符串。")
        t_start = time.monotonic()
        try:
            retrieved_chunks = self._search_collection(
                query=request.query,
                collection_name=request.collection_name,
                top_k=request.top_k,
            )
            return RagRetrieveResult(
                query=request.query,
                collection_name=request.collection_name,
                retrieved_chunks=tuple(retrieved_chunks),
                duration_s=time.monotonic() - t_start,
            )
        except (RagCollectionNotFoundError, RagRetrieveError):
            raise
        except RagError:
            raise
        except Exception as exc:
            raise RagRetrieveError(f"检索操作意外失败: {exc}") from exc

    def query(self, request: RagQueryRequest) -> RagQueryResult:
        """执行完整 RAG 流水线：检索 → 构建上下文 → 调用模型生成回答。

        Args:
            request (RagQueryRequest): 包含用户问题与生成参数的标准请求契约。
        Returns:
            RagQueryResult: 包含检索上下文、生成回答及 token 统计的结果契约。
        Raises:
            RagCollectionNotFoundError: 指定集合不存在时抛出。
            RagQueryError: 检索或模型调用失败时抛出。
        """
        if not request.query.strip():
            raise ValueError("RagQueryRequest.query 不能为空字符串。")
        t_start = time.monotonic()
        try:
            retrieve_result = self.retrieve(
                RagRetrieveRequest(
                    query=request.query,
                    collection_name=request.collection_name,
                    top_k=request.top_k,
                )
            )
            answer, prompt_tokens, completion_tokens = self._answer_generator.generate(
                query=request.query,
                chunks=list(retrieve_result.retrieved_chunks),
                max_tokens=request.answer_max_tokens,
                system_override=request.system_prompt_override,
            )
            return RagQueryResult(
                query=request.query,
                collection_name=request.collection_name,
                retrieved_chunks=retrieve_result.retrieved_chunks,
                answer=answer,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_s=time.monotonic() - t_start,
            )
        except (RagCollectionNotFoundError, RagQueryError):
            raise
        except RagError:
            raise
        except Exception as exc:
            raise RagQueryError(f"RAG 问答流水线意外失败: {exc}") from exc

    def drop_collection(self, collection_name: str) -> None:
        """删除指定名称的向量集合及其全部分块数据。

        Args:
            collection_name (str): 要删除的集合名称。
        Raises:
            RagCollectionNotFoundError: 集合不存在时抛出。
        """
        self._vector_store.drop(collection_name)

    def list_collections(self) -> list[str]:
        """列出当前向量存储中所有已建立的集合名称。

        Returns:
            list[str]: 集合名称列表；若尚未索引任何文档则返回空列表。
        """
        return self._vector_store.list_names()

    # ── 私有辅助方法（深度优先）──────────────────────────────────────────────

    def _chunk_documents(
        self,
        documents: list[RagDocument],
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[RagChunk]:
        """将文档列表批量切分为分块扁平列表。"""
        result: list[RagChunk] = []
        for doc in documents:
            result.extend(
                self._chunker.chunk(
                    document=doc,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            )
        return result

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """调用嵌入提供者执行批量文本向量化。"""
        try:
            return self._embedding_provider.embed_texts(texts)
        except RagIndexError:
            raise
        except Exception as exc:
            raise RagIndexError(f"嵌入向量生成失败: {exc}") from exc

    def _search_collection(
        self,
        query: str,
        collection_name: str,
        top_k: int,
    ) -> list[RagRetrievedChunk]:
        """嵌入查询文本并在指定集合中检索最相似分块。"""
        try:
            query_vectors = self._embed_texts([query])
        except RagIndexError as exc:
            raise RagRetrieveError(f"查询嵌入失败: {exc}") from exc

        try:
            hits = self._vector_store.search(
                name=collection_name,
                query_vector=query_vectors[0],
                top_k=top_k,
            )
        except RagCollectionNotFoundError:
            raise
        except Exception as exc:
            raise RagRetrieveError(f"向量检索失败: {exc}") from exc

        return [RagRetrievedChunk(chunk=chunk, score=score) for chunk, score in hits]

