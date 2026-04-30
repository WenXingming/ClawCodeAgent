"""RAG 模块单元测试。

覆盖范围：
  - DocumentChunker  : 滑动窗口切分的主流程、边界与异常。
  - VectorStore      : upsert / search / drop / list_names 及余弦相似度。
  - AnswerGenerator  : 消息构建、正常生成与模型调用失败异常。
  - RagGateway       : index_documents / retrieve / query / drop_collection /
                       list_collections 的主流程、边界与异常隔离。
"""

from __future__ import annotations

import heapq
import unittest
from unittest.mock import MagicMock, patch

from core_contracts.messaging import OneTurnResponse
from core_contracts.primitives import TokenUsage
from core_contracts.rag import (
    EmbeddingProvider,
    RagChunk,
    RagCollectionNotFoundError,
    RagDocument,
    RagIndexError,
    RagIndexRequest,
    RagQueryError,
    RagQueryRequest,
    RagRetrieveRequest,
    RagRetrievedChunk,
)
from rag.answer_generator import AnswerGenerator
from rag.chunker import DocumentChunker
from rag.rag_gateway import RagGateway
from rag.vector_store import VectorStore


# ─────────────────────────────────────────────────────────────────────────────
# 辅助工厂与 Stub
# ─────────────────────────────────────────────────────────────────────────────

def _make_doc(doc_id: str = 'doc-1', content: str = 'Hello world') -> RagDocument:
    """构建测试用文档 DTO。"""
    return RagDocument(doc_id=doc_id, content=content)


def _make_chunk(doc_id: str = 'doc-1', position: int = 0, content: str = 'Hello world') -> RagChunk:
    """构建测试用分块 DTO。"""
    return RagChunk(
        chunk_id=f'{doc_id}#{position}',
        doc_id=doc_id,
        content=content,
        position=position,
    )


def _stub_embedding_provider(dim: int = 3) -> EmbeddingProvider:
    """返回一个固定输出单位向量的 EmbeddingProvider stub。"""
    provider = MagicMock(spec=EmbeddingProvider)
    provider.embed_texts.side_effect = lambda texts: [[1.0] + [0.0] * (dim - 1)] * len(texts)
    return provider


def _stub_model_client(answer: str = '测试回答', input_tokens: int = 10, output_tokens: int = 5) -> MagicMock:
    """返回一个固定回答的 ModelClient stub。"""
    client = MagicMock()
    client.complete.return_value = OneTurnResponse(
        content=answer,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )
    return client


def _make_gateway(
    *,
    embedding_provider: EmbeddingProvider | None = None,
    model_client: MagicMock | None = None,
    answer: str = '测试回答',
) -> RagGateway:
    """装配一个使用 stub 依赖的 RagGateway 实例。"""
    return RagGateway(
        embedding_provider=embedding_provider or _stub_embedding_provider(),
        chunker=DocumentChunker(),
        vector_store=VectorStore(),
        answer_generator=AnswerGenerator(
            model_client=model_client or _stub_model_client(answer),
            model_config=None,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DocumentChunker 测试
# ─────────────────────────────────────────────────────────────────────────────

class DocumentChunkerChunkTests(unittest.TestCase):
    """验证 DocumentChunker.chunk 主流程与参数边界。"""

    def setUp(self) -> None:
        self.chunker = DocumentChunker()

    def test_chunk_short_content_produces_single_chunk(self) -> None:
        doc = _make_doc(content='Hello')
        chunks = self.chunker.chunk(doc, chunk_size=100, chunk_overlap=0)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].content, 'Hello')
        self.assertEqual(chunks[0].doc_id, 'doc-1')
        self.assertEqual(chunks[0].position, 0)
        self.assertEqual(chunks[0].chunk_id, 'doc-1#0')

    def test_chunk_empty_content_returns_empty_list(self) -> None:
        doc = _make_doc(content='   ')
        chunks = self.chunker.chunk(doc, chunk_size=10, chunk_overlap=0)
        self.assertEqual(chunks, [])

    def test_chunk_produces_multiple_chunks_with_overlap(self) -> None:
        content = 'A' * 20
        chunks = self.chunker.chunk(_make_doc(content=content), chunk_size=10, chunk_overlap=5)
        # step = 10 - 5 = 5；内容长 20，应生成多个分块
        self.assertGreater(len(chunks), 1)
        for idx, chunk in enumerate(chunks):
            self.assertEqual(chunk.position, idx)

    def test_chunk_metadata_inherited_from_document(self) -> None:
        doc = RagDocument(doc_id='d', content='text', metadata={'source': 'test'})
        chunks = self.chunker.chunk(doc, chunk_size=100, chunk_overlap=0)
        self.assertEqual(chunks[0].metadata['source'], 'test')

    def test_chunk_raises_on_invalid_chunk_size(self) -> None:
        doc = _make_doc(content='test')
        with self.assertRaises(RagIndexError):
            self.chunker.chunk(doc, chunk_size=0, chunk_overlap=0)

    def test_chunk_raises_when_overlap_gte_chunk_size(self) -> None:
        doc = _make_doc(content='test')
        with self.assertRaises(RagIndexError):
            self.chunker.chunk(doc, chunk_size=10, chunk_overlap=10)

    def test_chunk_finds_newline_break_point(self) -> None:
        content = ('A' * 8) + '\n' + ('B' * 8)
        chunks = self.chunker.chunk(_make_doc(content=content), chunk_size=10, chunk_overlap=0)
        # 应在换行符处切割，第一块以 A 结尾而不截断到最后
        self.assertTrue(all(c.content for c in chunks))

    def test_chunk_prefers_sentence_break_point_when_available(self) -> None:
        content = '第一句说明。第二句补充信息'
        chunks = self.chunker.chunk(_make_doc(content=content), chunk_size=7, chunk_overlap=0)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertTrue(chunks[0].content.endswith('。'))

    def test_chunk_avoids_breaking_english_word_when_forward_boundary_exists(self) -> None:
        content = 'important words and details'
        chunks = self.chunker.chunk(_make_doc(content=content), chunk_size=7, chunk_overlap=0)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(chunks[0].content, 'important')


# ─────────────────────────────────────────────────────────────────────────────
# VectorStore 测试
# ─────────────────────────────────────────────────────────────────────────────

class VectorStoreUpsertSearchTests(unittest.TestCase):
    """验证 VectorStore 的写入与检索主流程。"""

    def setUp(self) -> None:
        self.store = VectorStore()
        self.chunk = _make_chunk()
        self.vector = [1.0, 0.0, 0.0]

    def test_upsert_creates_collection_on_first_write(self) -> None:
        self.store.upsert('col', [self.chunk], [self.vector])
        self.assertIn('col', self.store.list_names())

    def test_upsert_appends_to_existing_collection(self) -> None:
        chunk2 = _make_chunk(position=1)
        self.store.upsert('col', [self.chunk], [self.vector])
        self.store.upsert('col', [chunk2], [[0.0, 1.0, 0.0]])
        results = self.store.search('col', [1.0, 0.0, 0.0], top_k=10)
        self.assertEqual(len(results), 2)

    def test_upsert_raises_when_counts_mismatch(self) -> None:
        with self.assertRaises(RagIndexError):
            self.store.upsert('col', [self.chunk], [[1.0], [0.0]])

    def test_search_returns_top_k_by_cosine_similarity(self) -> None:
        chunk_a = _make_chunk(position=0, content='A')
        chunk_b = _make_chunk(position=1, content='B')
        self.store.upsert('col', [chunk_a, chunk_b], [[1.0, 0.0], [0.0, 1.0]])
        results = self.store.search('col', [1.0, 0.0], top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0].content, 'A')
        self.assertAlmostEqual(results[0][1], 1.0)

    def test_search_returns_empty_when_top_k_not_positive(self) -> None:
        self.store.upsert('col', [self.chunk], [self.vector])
        self.assertEqual(self.store.search('col', [1.0, 0.0, 0.0], top_k=0), [])
        self.assertEqual(self.store.search('col', [1.0, 0.0, 0.0], top_k=-1), [])

    def test_search_uses_heapq_nlargest_for_top_k(self) -> None:
        chunk_a = _make_chunk(position=0, content='A')
        chunk_b = _make_chunk(position=1, content='B')
        self.store.upsert('col', [chunk_a, chunk_b], [[1.0, 0.0], [0.0, 1.0]])
        with patch('rag.vector_store.heapq.nlargest', wraps=heapq.nlargest) as spy_nlargest:
            results = self.store.search('col', [1.0, 0.0], top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0].content, 'A')
        spy_nlargest.assert_called_once()

    def test_search_empty_collection_returns_empty(self) -> None:
        self.store.upsert('col', [], [])
        results = self.store.search('col', [1.0, 0.0], top_k=5)
        self.assertEqual(results, [])

    def test_search_raises_collection_not_found(self) -> None:
        with self.assertRaises(RagCollectionNotFoundError) as ctx:
            self.store.search('nonexistent', [1.0], top_k=1)
        self.assertEqual(ctx.exception.collection_name, 'nonexistent')

    def test_drop_removes_collection(self) -> None:
        self.store.upsert('col', [self.chunk], [self.vector])
        self.store.drop('col')
        self.assertNotIn('col', self.store.list_names())

    def test_drop_raises_collection_not_found(self) -> None:
        with self.assertRaises(RagCollectionNotFoundError):
            self.store.drop('ghost')

    def test_list_names_reflects_all_collections(self) -> None:
        self.store.upsert('a', [self.chunk], [self.vector])
        self.store.upsert('b', [self.chunk], [self.vector])
        names = self.store.list_names()
        self.assertIn('a', names)
        self.assertIn('b', names)


class VectorStoreCosineSimilarityTests(unittest.TestCase):
    """验证余弦相似度计算的数学正确性与零向量边界。"""

    def setUp(self) -> None:
        self.store = VectorStore()

    def test_identical_vectors_score_one(self) -> None:
        score = self.store._cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        self.assertAlmostEqual(score, 1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        score = self.store._cosine_similarity([1.0, 0.0], [0.0, 1.0])
        self.assertAlmostEqual(score, 0.0)

    def test_zero_vector_returns_zero(self) -> None:
        score = self.store._cosine_similarity([0.0, 0.0], [1.0, 1.0])
        self.assertEqual(score, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# AnswerGenerator 测试
# ─────────────────────────────────────────────────────────────────────────────

class AnswerGeneratorGenerateTests(unittest.TestCase):
    """验证 AnswerGenerator.generate 主流程、消息构建与异常转译。"""

    def _make_retrieved_chunk(self, content: str = 'ctx', score: float = 0.9) -> RagRetrievedChunk:
        return RagRetrievedChunk(chunk=_make_chunk(content=content), score=score)

    def test_generate_returns_answer_and_token_counts(self) -> None:
        client = _stub_model_client(answer='回答内容', input_tokens=20, output_tokens=8)
        gen = AnswerGenerator(model_client=client, model_config=None)
        answer, in_tok, out_tok = gen.generate(
            query='问题',
            chunks=[self._make_retrieved_chunk()],
            max_tokens=100,
            system_override=None,
        )
        self.assertEqual(answer, '回答内容')
        self.assertEqual(in_tok, 20)
        self.assertEqual(out_tok, 8)

    def test_generate_passes_system_override_to_model(self) -> None:
        client = _stub_model_client()
        gen = AnswerGenerator(model_client=client, model_config=None)
        gen.generate(
            query='q',
            chunks=[],
            max_tokens=50,
            system_override='自定义系统提示词',
        )
        messages_sent = client.complete.call_args[0][0]
        self.assertEqual(messages_sent[0]['content'], '自定义系统提示词')

    def test_generate_uses_default_system_prompt_when_no_override(self) -> None:
        client = _stub_model_client()
        gen = AnswerGenerator(model_client=client, model_config=None)
        gen.generate(query='q', chunks=[], max_tokens=50, system_override=None)
        messages_sent = client.complete.call_args[0][0]
        self.assertIn('知识问答助手', messages_sent[0]['content'])

    def test_generate_raises_rag_query_error_on_model_exception(self) -> None:
        client = MagicMock()
        client.complete.side_effect = RuntimeError('network error')
        gen = AnswerGenerator(model_client=client, model_config=None)
        with self.assertRaises(RagQueryError):
            gen.generate(query='q', chunks=[], max_tokens=50, system_override=None)

    def test_generate_raises_on_empty_model_response(self) -> None:
        client = MagicMock()
        client.complete.return_value = OneTurnResponse(
            content='   ',
            usage=TokenUsage(input_tokens=5, output_tokens=0),
        )
        gen = AnswerGenerator(model_client=client, model_config=None)
        with self.assertRaises(RagQueryError):
            gen.generate(query='q', chunks=[], max_tokens=50, system_override=None)

    def test_format_context_with_empty_chunks_returns_placeholder(self) -> None:
        gen = AnswerGenerator(model_client=MagicMock(), model_config=None)
        text = gen._format_context([])
        self.assertIn('未找到', text)

    def test_format_context_includes_chunk_content_and_score(self) -> None:
        gen = AnswerGenerator(model_client=MagicMock(), model_config=None)
        chunk = self._make_retrieved_chunk(content='重要段落', score=0.85)
        text = gen._format_context([chunk])
        self.assertIn('重要段落', text)
        self.assertIn('0.850', text)


# ─────────────────────────────────────────────────────────────────────────────
# RagGateway 集成测试（全部依赖 stub/mock，无外部 I/O）
# ─────────────────────────────────────────────────────────────────────────────

class RagGatewayIndexDocumentsTests(unittest.TestCase):
    """验证 RagGateway.index_documents 主流程与边界条件。"""

    def test_index_returns_correct_counts(self) -> None:
        gw = _make_gateway()
        doc = _make_doc(content='Python 是一门动态语言。' * 5)
        req = RagIndexRequest(documents=(doc,), collection_name='test', chunk_size=20, chunk_overlap=0)
        result = gw.index_documents(req)
        self.assertEqual(result.docs_indexed, 1)
        self.assertGreater(result.chunks_created, 0)
        self.assertEqual(result.collection_name, 'test')
        self.assertGreaterEqual(result.duration_s, 0.0)

    def test_index_empty_documents_raises_value_error(self) -> None:
        gw = _make_gateway()
        req = RagIndexRequest(documents=(), collection_name='col')
        with self.assertRaises(ValueError):
            gw.index_documents(req)

    def test_index_embedding_failure_raises_rag_index_error(self) -> None:
        provider = MagicMock(spec=EmbeddingProvider)
        provider.embed_texts.side_effect = OSError('network unreachable')
        gw = _make_gateway(embedding_provider=provider)
        req = RagIndexRequest(documents=(_make_doc(content='text'),), collection_name='c')
        with self.assertRaises(RagIndexError):
            gw.index_documents(req)

    def test_index_multiple_documents_aggregates_chunks(self) -> None:
        gw = _make_gateway()
        docs = tuple(_make_doc(doc_id=f'd{i}', content='word ' * 10) for i in range(3))
        req = RagIndexRequest(documents=docs, collection_name='multi', chunk_size=15, chunk_overlap=0)
        result = gw.index_documents(req)
        self.assertEqual(result.docs_indexed, 3)
        self.assertGreater(result.chunks_created, 0)


class RagGatewayRetrieveTests(unittest.TestCase):
    """验证 RagGateway.retrieve 检索流程与错误路径。"""

    def _indexed_gateway(self) -> RagGateway:
        gw = _make_gateway()
        req = RagIndexRequest(
            documents=(_make_doc(content='机器学习是人工智能的子领域。'),),
            collection_name='ai',
            chunk_size=50,
            chunk_overlap=0,
        )
        gw.index_documents(req)
        return gw

    def test_retrieve_returns_result_with_chunks(self) -> None:
        gw = self._indexed_gateway()
        result = gw.retrieve(RagRetrieveRequest(query='机器学习', collection_name='ai', top_k=3))
        self.assertEqual(result.query, '机器学习')
        self.assertEqual(result.collection_name, 'ai')
        self.assertIsInstance(result.retrieved_chunks, tuple)

    def test_retrieve_respects_top_k_limit(self) -> None:
        gw = _make_gateway()
        # 索引一篇生成多个分块的文档
        content = '段落内容。' * 30
        gw.index_documents(RagIndexRequest(
            documents=(_make_doc(content=content),),
            collection_name='big',
            chunk_size=10,
            chunk_overlap=0,
        ))
        result = gw.retrieve(RagRetrieveRequest(query='段落', collection_name='big', top_k=2))
        self.assertLessEqual(len(result.retrieved_chunks), 2)

    def test_retrieve_raises_on_empty_query(self) -> None:
        gw = self._indexed_gateway()
        with self.assertRaises(ValueError):
            gw.retrieve(RagRetrieveRequest(query='  ', collection_name='ai'))

    def test_retrieve_raises_collection_not_found(self) -> None:
        gw = _make_gateway()
        with self.assertRaises(RagCollectionNotFoundError):
            gw.retrieve(RagRetrieveRequest(query='hello', collection_name='ghost'))


class RagGatewayQueryTests(unittest.TestCase):
    """验证 RagGateway.query 完整 RAG 流水线与异常转译。"""

    def _indexed_gateway(self, answer: str = 'AI 是一门学科。') -> RagGateway:
        gw = _make_gateway(answer=answer)
        gw.index_documents(RagIndexRequest(
            documents=(_make_doc(content='人工智能（AI）是计算机科学的重要分支。'),),
            collection_name='kb',
            chunk_size=50,
            chunk_overlap=0,
        ))
        return gw

    def test_query_returns_answer_and_retrieved_chunks(self) -> None:
        gw = self._indexed_gateway(answer='这是模型回答。')
        result = gw.query(RagQueryRequest(query='什么是AI', collection_name='kb', top_k=3))
        self.assertEqual(result.answer, '这是模型回答。')
        self.assertEqual(result.query, '什么是AI')
        self.assertIsInstance(result.retrieved_chunks, tuple)
        self.assertGreater(result.prompt_tokens, 0)
        self.assertGreater(result.completion_tokens, 0)

    def test_query_raises_on_empty_query(self) -> None:
        gw = self._indexed_gateway()
        with self.assertRaises(ValueError):
            gw.query(RagQueryRequest(query='', collection_name='kb'))

    def test_query_raises_collection_not_found(self) -> None:
        gw = _make_gateway()
        with self.assertRaises(RagCollectionNotFoundError):
            gw.query(RagQueryRequest(query='test', collection_name='missing'))

    def test_query_propagates_model_failure_as_rag_query_error(self) -> None:
        client = MagicMock()
        client.complete.side_effect = RuntimeError('timeout')
        gw = RagGateway(
            embedding_provider=_stub_embedding_provider(),
            chunker=DocumentChunker(),
            vector_store=VectorStore(),
            answer_generator=AnswerGenerator(model_client=client, model_config=None),
        )
        gw.index_documents(RagIndexRequest(
            documents=(_make_doc(content='some content'),),
            collection_name='c',
        ))
        with self.assertRaises(RagQueryError):
            gw.query(RagQueryRequest(query='test', collection_name='c'))


class RagGatewayCollectionManagementTests(unittest.TestCase):
    """验证 RagGateway 集合管理操作。"""

    def test_list_collections_empty_before_indexing(self) -> None:
        gw = _make_gateway()
        self.assertEqual(gw.list_collections(), [])

    def test_list_collections_after_indexing(self) -> None:
        gw = _make_gateway()
        gw.index_documents(RagIndexRequest(
            documents=(_make_doc(content='hello'),),
            collection_name='my-col',
        ))
        self.assertIn('my-col', gw.list_collections())

    def test_drop_collection_removes_it_from_list(self) -> None:
        gw = _make_gateway()
        gw.index_documents(RagIndexRequest(
            documents=(_make_doc(content='hello'),),
            collection_name='temp',
        ))
        gw.drop_collection('temp')
        self.assertNotIn('temp', gw.list_collections())

    def test_drop_nonexistent_collection_raises(self) -> None:
        gw = _make_gateway()
        with self.assertRaises(RagCollectionNotFoundError):
            gw.drop_collection('does-not-exist')

    def test_retrieve_after_drop_raises_collection_not_found(self) -> None:
        gw = _make_gateway()
        gw.index_documents(RagIndexRequest(
            documents=(_make_doc(content='data'),),
            collection_name='drop-me',
        ))
        gw.drop_collection('drop-me')
        with self.assertRaises(RagCollectionNotFoundError):
            gw.retrieve(RagRetrieveRequest(query='data', collection_name='drop-me'))


# ─────────────────────────────────────────────────────────────────────────────
# build_rag_gateway 工厂测试
# ─────────────────────────────────────────────────────────────────────────────

class BuildRagGatewayTests(unittest.TestCase):
    """验证公开工厂函数 build_rag_gateway 能正确装配 RagGateway。"""

    def test_factory_returns_rag_gateway_instance(self) -> None:
        from rag import build_rag_gateway
        provider = _stub_embedding_provider()
        client = _stub_model_client()
        gw = build_rag_gateway(
            embedding_provider=provider,
            model_client=client,
        )
        self.assertIsInstance(gw, RagGateway)

    def test_factory_gateway_can_index_and_retrieve(self) -> None:
        from rag import build_rag_gateway
        provider = _stub_embedding_provider()
        client = _stub_model_client()
        gw = build_rag_gateway(embedding_provider=provider, model_client=client)
        gw.index_documents(RagIndexRequest(
            documents=(_make_doc(content='factory test content'),),
            collection_name='fac',
        ))
        result = gw.retrieve(RagRetrieveRequest(query='factory', collection_name='fac'))
        self.assertIsInstance(result.retrieved_chunks, tuple)


if __name__ == '__main__':
    unittest.main()
