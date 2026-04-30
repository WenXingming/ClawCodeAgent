"""RAG（检索增强生成）模块公开入口。

外部代码只允许从本文件导入，不得直接导入内部实现类。

公开导出：
  - RagGateway        : RAG 模块的唯一门面。
  - build_rag_gateway : 标准装配工厂，接收外部依赖并完成全链路注入。

所有请求/结果契约与异常类型均定义在 src/core_contracts/rag.py，
请直接从 core_contracts.rag 导入，无需经过本模块转发。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rag.rag_gateway import RagGateway

if TYPE_CHECKING:
    from core_contracts.model import ModelClient, ModelConfig
    from core_contracts.rag import EmbeddingProvider

__all__ = ['RagGateway', 'build_rag_gateway']


def build_rag_gateway(
    *,
    embedding_provider: 'EmbeddingProvider',
    model_client: 'ModelClient',
    model_config: 'ModelConfig | None' = None,
) -> RagGateway:
    """标准工厂函数：装配并返回一个开箱即用的 RagGateway 实例。

    调用方只需提供三个外部依赖，工厂负责构造全部内部组件并完成依赖注入，
    外部代码无需感知 DocumentChunker / VectorStore / AnswerGenerator。

    Args:
        embedding_provider (EmbeddingProvider): 文本嵌入向量提供者，须实现 EmbeddingProvider 协议。
        model_client (ModelClient): 模型调用客户端，须实现 ModelClient 协议的 complete() 方法。
        model_config (ModelConfig | None): 可选的模型配置覆盖；为 None 时引擎使用客户端默认配置。
    Returns:
        RagGateway: 已完整装配的 RAG 门面网关实例。
    """
    from rag.answer_generator import AnswerGenerator
    from rag.chunker import DocumentChunker
    from rag.vector_store import VectorStore

    return RagGateway(
        embedding_provider=embedding_provider,
        chunker=DocumentChunker(),
        vector_store=VectorStore(),
        answer_generator=AnswerGenerator(
            model_client=model_client,
            model_config=model_config,
        ),
    )
