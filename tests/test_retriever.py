"""测试多路检索引擎 —— RetrievalEngine 单元测试。

覆盖场景：
    - IntentDirectedChannel 检索
    - GlobalVectorChannel 检索
    - DeduplicatePostProcessor 去重
    - RerankPostProcessor 重排序（Mock）
    - RetrievalEngine 完整流程
    - EmbeddingService 失败时的降级
"""

from __future__ import annotations

import pytest

from ragent.rag.retrieval.retriever import (
    DeduplicatePostProcessor,
    GlobalVectorChannel,
    IntentDirectedChannel,
    RerankPostProcessor,
    RetrievalEngine,
    SearchResult,
)
from ragent.rag.intent.intent_classifier import IntentNode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_intent_node() -> IntentNode:
    """创建测试用的意图节点。"""
    return IntentNode(
        intent_code="TOPIC_RAG",
        name="RAG检索增强生成",
        level=2,
        parent_code="DOMAIN_TECH",
        examples=["什么是RAG"],
        collection_name="rag_kb",
    )


class MockEmbeddingService:
    """模拟向量嵌入服务。"""

    def __init__(self, dimension: int = 128) -> None:
        self._dimension = dimension
        self._should_fail = False

    async def embed(self, text: str, **kwargs) -> list[float]:
        if self._should_fail:
            raise RuntimeError("嵌入服务不可用")
        return [0.1] * self._dimension

    async def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * self._dimension for _ in texts]

    def set_fail(self, should_fail: bool) -> None:
        self._should_fail = should_fail


# ---------------------------------------------------------------------------
# 测试 IntentDirectedChannel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intent_directed_channel():
    """意图定向通道应返回 Mock 结果。"""
    intent = make_intent_node()
    channel = IntentDirectedChannel(intent)

    results = await channel.search([0.1] * 128, top_k=5)

    assert len(results) <= 5
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.source_channel.startswith("intent-") for r in results)
    assert all(r.metadata.get("intent_code") == "TOPIC_RAG" for r in results)


@pytest.mark.asyncio
async def test_intent_directed_channel_top_k():
    """意图定向通道应遵守 top_k 限制。"""
    intent = make_intent_node()
    channel = IntentDirectedChannel(intent)

    results = await channel.search([0.1] * 128, top_k=2)
    assert len(results) <= 2


# ---------------------------------------------------------------------------
# 测试 GlobalVectorChannel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_vector_channel():
    """全局向量通道应返回 Mock 结果。"""
    channel = GlobalVectorChannel(collections=["kb1", "kb2"])

    results = await channel.search([0.1] * 128, top_k=10)

    assert len(results) <= 5
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.source_channel == "global-vector" for r in results)


@pytest.mark.asyncio
async def test_global_vector_channel_default():
    """未指定集合时应使用默认集合。"""
    channel = GlobalVectorChannel()
    results = await channel.search([0.1] * 128)
    assert len(results) > 0


# ---------------------------------------------------------------------------
# 测试 DeduplicatePostProcessor
# ---------------------------------------------------------------------------


def test_dedup_no_duplicates():
    """无重复内容时应保持不变。"""
    processor = DeduplicatePostProcessor()
    results = [
        SearchResult(chunk_id="1", content="内容A", score=0.9, source_channel="ch1"),
        SearchResult(chunk_id="2", content="内容B", score=0.8, source_channel="ch1"),
        SearchResult(chunk_id="3", content="内容C", score=0.7, source_channel="ch2"),
    ]

    deduped = processor.process(results)

    assert len(deduped) == 3


def test_dedup_with_duplicates():
    """有重复内容时应去重，保留最高分。"""
    processor = DeduplicatePostProcessor()
    results = [
        SearchResult(chunk_id="1", content="相同内容", score=0.9, source_channel="ch1"),
        SearchResult(chunk_id="2", content="相同内容", score=0.95, source_channel="ch2"),
        SearchResult(chunk_id="3", content="不同内容", score=0.8, source_channel="ch1"),
    ]

    deduped = processor.process(results)

    assert len(deduped) == 2
    # 相同内容的应保留分数高的那个
    same_content_items = [r for r in deduped if r.content == "相同内容"]
    assert len(same_content_items) == 1
    assert same_content_items[0].score == 0.95


def test_dedup_sorted_by_score():
    """去重后应按分数降序排列。"""
    processor = DeduplicatePostProcessor()
    results = [
        SearchResult(chunk_id="1", content="内容A", score=0.7, source_channel="ch1"),
        SearchResult(chunk_id="2", content="内容B", score=0.9, source_channel="ch1"),
        SearchResult(chunk_id="3", content="内容C", score=0.8, source_channel="ch2"),
    ]

    deduped = processor.process(results)

    scores = [r.score for r in deduped]
    assert scores == sorted(scores, reverse=True)


def test_dedup_empty_input():
    """空输入应返回空列表。"""
    processor = DeduplicatePostProcessor()
    assert processor.process([]) == []


# ---------------------------------------------------------------------------
# 测试 RerankPostProcessor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_mock():
    """Mock 重排序应直接返回原始结果（截取 top_k）。"""
    processor = RerankPostProcessor()
    results = [
        SearchResult(chunk_id=str(i), content=f"内容{i}", score=0.9 - i * 0.1, source_channel="ch")
        for i in range(5)
    ]

    reranked = await processor.process("测试查询", results, top_k=3)

    assert len(reranked) == 3


@pytest.mark.asyncio
async def test_rerank_empty_input():
    """空输入应返回空列表。"""
    processor = RerankPostProcessor()
    reranked = await processor.process("测试查询", [], top_k=3)
    assert reranked == []


# ---------------------------------------------------------------------------
# 测试 SearchResult
# ---------------------------------------------------------------------------


def test_search_result_auto_hash():
    """SearchResult 应自动计算 content_hash。"""
    r1 = SearchResult(chunk_id="1", content="测试内容", score=0.9)
    r2 = SearchResult(chunk_id="2", content="测试内容", score=0.8)

    assert r1.content_hash == r2.content_hash
    assert r1.content_hash != ""


def test_search_result_different_content():
    """不同内容应有不同的 hash。"""
    r1 = SearchResult(chunk_id="1", content="内容A", score=0.9)
    r2 = SearchResult(chunk_id="2", content="内容B", score=0.8)

    assert r1.content_hash != r2.content_hash


# ---------------------------------------------------------------------------
# 测试 RetrievalEngine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_with_intent():
    """有意图时应同时使用定向通道和全局通道。"""
    embedding = MockEmbeddingService()
    engine = RetrievalEngine(embedding)
    intent = make_intent_node()

    results = await engine.search("什么是RAG？", intent=intent, top_k=10)

    assert isinstance(results, list)
    assert len(results) > 0


@pytest.mark.asyncio
async def test_engine_without_intent():
    """无意图时应使用全局通道。"""
    embedding = MockEmbeddingService()
    engine = RetrievalEngine(embedding)

    results = await engine.search("什么是RAG？", intent=None, top_k=10)

    assert isinstance(results, list)
    assert len(results) > 0


@pytest.mark.asyncio
async def test_engine_embedding_failure():
    """嵌入服务失败时应返回空列表。"""
    embedding = MockEmbeddingService()
    embedding.set_fail(True)
    engine = RetrievalEngine(embedding)

    results = await engine.search("测试问题", intent=None)

    assert results == []


@pytest.mark.asyncio
async def test_engine_custom_processors():
    """自定义后处理器应被正确使用。"""
    embedding = MockEmbeddingService()
    dedup = DeduplicatePostProcessor()
    rerank = RerankPostProcessor()
    engine = RetrievalEngine(embedding, dedup_processor=dedup, rerank_processor=rerank)

    results = await engine.search("测试问题", top_k=5)

    assert isinstance(results, list)
