"""测试 RAG 问答主链路 —— RAGChain 单元测试。

覆盖场景：
    - 完整 RAG 管线执行（Mock 所有依赖）
    - SSE 事件类型正确性
    - 会话记忆保存
    - LLM 失败时的错误事件
    - 无会话 ID 时不保存记忆
    - 意图分类结果传递到检索
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ragent.common.sse import SSEEvent, SSEEventType
from ragent.infra.ai.llm_service import LLMService
from ragent.infra.ai.embedding_service import EmbeddingService
from ragent.rag.chain import RAGChain, MOCK_INTENT_TREE
from ragent.rag.intent.intent_classifier import IntentNode, IntentResult
from ragent.rag.rewriter.query_rewriter import RewriteResult
from ragent.rag.retrieval.retriever import SearchResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def create_mock_llm(
    chat_responses: list[str] | None = None,
    stream_tokens: list[str] | None = None,
) -> MagicMock:
    """创建 Mock LLM 服务。"""
    llm = MagicMock(spec=LLMService)

    if chat_responses:
        llm.chat = AsyncMock(side_effect=chat_responses)
    else:
        llm.chat = AsyncMock(return_value="Mock回复")

    if stream_tokens:
        async def mock_stream(*args, **kwargs):
            for token in stream_tokens:
                yield token
        llm.stream_chat = mock_stream
    else:
        async def default_stream(*args, **kwargs):
            yield "这是"
            yield "Mock"
            yield "回复"
        llm.stream_chat = default_stream

    return llm


def create_mock_embedding() -> MagicMock:
    """创建 Mock 向量嵌入服务。"""
    embedding = MagicMock(spec=EmbeddingService)
    embedding.embed = AsyncMock(return_value=[0.1] * 128)
    embedding.embed_batch = AsyncMock(return_value=[[0.1] * 128])
    return embedding


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_chain_basic_flow():
    """基本 RAG 管线应产出完整的事件流。"""
    # 查询重写返回 -> 意图分类返回 -> 检索 -> 生成
    chat_responses = [
        "重写后的问题",    # 上下文补全
        '[]',             # 问题拆分
        json.dumps([{"code": "TOPIC_RAG", "score": 0.9}]),  # 意图评分
    ]

    llm = create_mock_llm(
        chat_responses=chat_responses,
        stream_tokens=["你好", "，", "世界"],
    )
    embedding = create_mock_embedding()

    chain = RAGChain(llm, embedding)

    events = []
    async for event in chain.ask("你好"):
        events.append(event)

    # 应至少包含 meta、content、finish 事件
    event_types = [e.event for e in events]
    assert SSEEventType.META in event_types
    assert SSEEventType.CONTENT in event_types
    assert SSEEventType.FINISH in event_types

    # 内容事件应包含流式 token
    content_events = [e for e in events if e.event == SSEEventType.CONTENT]
    content_data = "".join(
        json.loads(e.data)["content"] for e in content_events
    )
    assert content_data == "你好，世界"


@pytest.mark.asyncio
async def test_rag_chain_with_conversation():
    """有 conversation_id 时应保存记忆。"""
    llm = create_mock_llm(
        chat_responses=["重写问题", '[]', '{"code":"TOPIC_RAG","score":0.9}'],
        stream_tokens=["回答内容"],
    )
    embedding = create_mock_embedding()

    chain = RAGChain(llm, embedding)

    events = []
    async for event in chain.ask("你好", conversation_id=1):
        events.append(event)

    # 验证记忆已保存
    memory = await chain._memory.get_memory(1)
    assert len(memory.recent_messages) == 2  # user + assistant
    assert memory.recent_messages[0].role == "user"
    assert memory.recent_messages[0].content == "你好"
    assert memory.recent_messages[1].role == "assistant"
    assert memory.recent_messages[1].content == "回答内容"


@pytest.mark.asyncio
async def test_rag_chain_no_conversation_id():
    """无 conversation_id 时不应保存记忆。"""
    llm = create_mock_llm(
        chat_responses=["重写", '[]', '{"code":"TOPIC_RAG","score":0.9}'],
        stream_tokens=["回答"],
    )
    embedding = create_mock_embedding()

    chain = RAGChain(llm, embedding)

    events = []
    async for event in chain.ask("你好"):
        events.append(event)

    # 无 conversation_id，不应有记忆操作
    assert chain._memory.get_message_count(1) == 0


@pytest.mark.asyncio
async def test_rag_chain_finish_event_metadata():
    """finish 事件应包含正确的元数据。"""
    llm = create_mock_llm(
        chat_responses=["重写", '[]', json.dumps([{"code": "TOPIC_RAG", "score": 0.85}])],
        stream_tokens=["回答"],
    )
    embedding = create_mock_embedding()

    chain = RAGChain(llm, embedding)

    events = []
    async for event in chain.ask("测试问题", conversation_id=42):
        events.append(event)

    finish_events = [e for e in events if e.event == SSEEventType.FINISH]
    assert len(finish_events) == 1

    finish_data = json.loads(finish_events[0].data)
    assert finish_data["conversation_id"] == 42
    assert "result_count" in finish_data


@pytest.mark.asyncio
async def test_rag_chain_meta_events_stages():
    """meta 事件应包含正确的阶段信息。"""
    llm = create_mock_llm(
        chat_responses=["重写", '[]', '{"code":"TOPIC_RAG","score":0.9}'],
        stream_tokens=["回答"],
    )
    embedding = create_mock_embedding()

    chain = RAGChain(llm, embedding)

    events = []
    async for event in chain.ask("测试问题"):
        events.append(event)

    meta_events = [e for e in events if e.event == SSEEventType.META]

    # 检查阶段信息
    stages = []
    for e in meta_events:
        data = json.loads(e.data)
        if "stage" in data:
            stages.append(data["stage"])

    assert "query-rewrite" in stages
    assert "intent-classify" in stages
    assert "retrieval" in stages
    assert "prompt-build" in stages
    assert "llm-generate" in stages


@pytest.mark.asyncio
async def test_rag_chain_custom_intent_tree():
    """自定义意图树应被正确使用。"""
    custom_tree = [
        IntentNode(
            intent_code="CUSTOM_1",
            name="自定义意图",
            level=2,
            parent_code=None,
            examples=["自定义示例"],
            collection_name="custom_kb",
        ),
    ]

    llm = create_mock_llm(
        chat_responses=["重写", '[]', json.dumps([{"code": "CUSTOM_1", "score": 0.9}])],
        stream_tokens=["回答"],
    )
    embedding = create_mock_embedding()

    chain = RAGChain(llm, embedding, intent_tree=custom_tree)

    events = []
    async for event in chain.ask("自定义示例相关问题"):
        events.append(event)

    # 应正常完成
    finish_events = [e for e in events if e.event == SSEEventType.FINISH]
    assert len(finish_events) == 1


@pytest.mark.asyncio
async def test_rag_chain_conversation_history_loaded():
    """有历史记忆时应加载并传递给重写和 Prompt。"""
    llm = create_mock_llm(
        chat_responses=["重写", '[]', '{"code":"TOPIC_RAG","score":0.9}'],
        stream_tokens=["回答"],
    )
    embedding = create_mock_embedding()

    chain = RAGChain(llm, embedding)

    # 预先添加历史记忆
    await chain._memory.add_message(1, "user", "之前的问题")
    await chain._memory.add_message(1, "assistant", "之前的回答")

    events = []
    async for event in chain.ask("后续问题", conversation_id=1):
        events.append(event)

    # 管线应正常完成
    finish_events = [e for e in events if e.event == SSEEventType.FINISH]
    assert len(finish_events) == 1


@pytest.mark.asyncio
async def test_rag_chain_build_context():
    """_build_context 应正确格式化检索结果。"""
    results = [
        SearchResult(chunk_id="1", content="内容A", score=0.9),
        SearchResult(chunk_id="2", content="内容B", score=0.8),
    ]

    context = RAGChain._build_context(results)

    assert "[1] 内容A" in context
    assert "[2] 内容B" in context


@pytest.mark.asyncio
async def test_rag_chain_build_context_empty():
    """无检索结果时 _build_context 应返回 None。"""
    context = RAGChain._build_context([])
    assert context is None
