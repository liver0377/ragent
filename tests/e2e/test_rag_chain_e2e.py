"""
E2E 测试 —— RAGChain 完整管线端到端测试。

测试场景：
    1. test_rag_chain_full_pipeline — 完整管线执行（重写→分类→检索→Prompt→生成）
    2. test_rag_chain_with_memory — 带会话记忆的对话
    3. test_rag_chain_retrieval_failure — 检索失败时的容错
    4. test_rag_chain_llm_failure — LLM 生成失败时的容错
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ragent.common.sse import SSEEvent, SSEEventType, sse_content, sse_error, sse_finish, sse_meta
from ragent.infra.ai.embedding_service import EmbeddingService
from ragent.infra.ai.llm_service import LLMService
from ragent.infra.ai.models import ModelCandidate, ModelConfig, ModelConfigManager
from ragent.infra.ai.model_selector import ModelSelector
from ragent.rag.chain import RAGChain
from ragent.rag.intent.intent_classifier import IntentNode, IntentResult
from ragent.rag.retrieval.retriever import SearchResult
from ragent.rag.rewriter.query_rewriter import RewriteResult


# ---------------------------------------------------------------------------
# 辅助函数与 Fixtures
# ---------------------------------------------------------------------------


def _make_llm_service() -> LLMService:
    """创建测试用 LLMService（带 mock 配置）。"""
    config = ModelConfig(
        chat_models=[ModelCandidate(model_name="test-model", provider="test", priority=0)],
        embedding_models=[ModelCandidate(model_name="test-emb", provider="test", priority=0)],
    )
    mgr = ModelConfigManager(config=config)
    selector = ModelSelector(mgr)
    return LLMService(mgr, selector)


def _make_embedding_service() -> EmbeddingService:
    """创建测试用 EmbeddingService（带 mock 配置）。"""
    config = ModelConfig(
        chat_models=[ModelCandidate(model_name="test-model", provider="test", priority=0)],
        embedding_models=[ModelCandidate(model_name="test-emb", provider="test", priority=0)],
    )
    mgr = ModelConfigManager(config=config)
    selector = ModelSelector(mgr)
    return EmbeddingService(mgr, selector)


@pytest.fixture
def llm_service() -> LLMService:
    """LLM 服务 fixture。"""
    return _make_llm_service()


@pytest.fixture
def embedding_service() -> EmbeddingService:
    """Embedding 服务 fixture。"""
    return _make_embedding_service()


def _collect_events(chain_events: Any) -> list[SSEEvent]:
    """收集异步生成器的所有 SSE 事件。"""
    return list(chain_events)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


async def test_rag_chain_full_pipeline(
    llm_service: LLMService,
    embedding_service: EmbeddingService,
) -> None:
    """mock LLM 和 Embedding，验证完整管线执行（重写→分类→检索→Prompt→生成），检查 SSE 事件序列。"""
    chain = RAGChain(llm_service, embedding_service)

    # Mock 各子模块
    with patch.object(chain._rewriter, "rewrite", new_callable=AsyncMock) as mock_rewrite, \
         patch.object(chain._classifier, "classify", new_callable=AsyncMock) as mock_classify, \
         patch.object(chain._retriever, "search", new_callable=AsyncMock) as mock_search, \
         patch.object(chain._llm, "stream_chat") as mock_stream:

        # 配置 mock 返回值
        mock_rewrite.return_value = RewriteResult(
            rewritten="什么是RAG检索增强生成",
            sub_questions=[],
            normalized_terms={},
        )

        mock_intent = IntentNode(
            intent_code="TOPIC_RAG",
            name="RAG检索增强生成",
            level=2,
            parent_code="DOMAIN_TECH",
            collection_name="rag_knowledge",
        )
        mock_classify.return_value = IntentResult(
            intent=mock_intent,
            confidence=0.9,
            candidates=[(mock_intent, 0.9)],
            needs_clarification=False,
        )

        mock_search.return_value = [
            SearchResult(
                chunk_id="chunk_001",
                content="RAG是一种结合检索和生成的技术",
                score=0.95,
                metadata={"source": "test"},
                source_channel="test_channel",
            ),
        ]

        # 模拟流式 token
        async def mock_stream_generator(messages):
            for token in ["RAG是", "检索增强", "生成技术"]:
                yield token

        mock_stream.return_value = mock_stream_generator([])

        # 执行管线并收集事件
        events: list[SSEEvent] = []
        async for event in chain.ask("什么是RAG？"):
            events.append(event)

    # 验证事件序列
    event_types = [e.event for e in events]

    # 应包含 meta 事件（多个阶段）
    assert "meta" in event_types, "应包含 meta 事件"

    # 应包含 content 事件
    assert "content" in event_types, "应包含 content 事件"

    # 应包含 finish 事件
    assert "finish" in event_types, "应包含 finish 事件"

    # 验证 meta→content→finish 的顺序
    meta_indices = [i for i, e in enumerate(events) if e.event == "meta"]
    content_indices = [i for i, e in enumerate(events) if e.event == "content"]
    finish_indices = [i for i, e in enumerate(events) if e.event == "finish"]

    assert min(meta_indices) < min(content_indices), "meta 应在 content 之前"
    assert max(content_indices) < min(finish_indices), "content 应在 finish 之前"

    # 验证 content 数据
    content_events = [e for e in events if e.event == "content"]
    full_text = "".join(e.data for e in content_events if e.data)
    assert "RAG是" in full_text
    assert "检索增强" in full_text

    # 验证 finish 事件包含预期字段
    finish_event = [e for e in events if e.event == "finish"][0]
    finish_data = json.loads(finish_event.data) if finish_event.data else {}
    assert "result_count" in finish_data


async def test_rag_chain_with_memory(
    llm_service: LLMService,
    embedding_service: EmbeddingService,
) -> None:
    """带会话记忆的对话。"""
    chain = RAGChain(llm_service, embedding_service)
    conversation_id = 99999

    # Mock 子模块
    with patch.object(chain._rewriter, "rewrite", new_callable=AsyncMock) as mock_rewrite, \
         patch.object(chain._classifier, "classify", new_callable=AsyncMock) as mock_classify, \
         patch.object(chain._retriever, "search", new_callable=AsyncMock) as mock_search, \
         patch.object(chain._llm, "stream_chat") as mock_stream, \
         patch.object(chain._memory, "get_memory", new_callable=AsyncMock) as mock_get_memory, \
         patch.object(chain._memory, "add_message", new_callable=AsyncMock) as mock_add_msg, \
         patch.object(chain._memory, "should_summarize", new_callable=AsyncMock) as mock_should_sum, \
         patch.object(chain._memory, "summarize", new_callable=AsyncMock) as mock_summarize:

        from ragent.rag.memory.session_memory import SessionMemory
        mock_get_memory.return_value = SessionMemory(
            conversation_id=conversation_id,
            summary="之前讨论了RAG技术",
            recent_messages=[],
        )

        mock_rewrite.return_value = RewriteResult(
            rewritten="RAG的最新进展",
            sub_questions=[],
            normalized_terms={},
        )
        mock_classify.return_value = IntentResult(
            intent=None,
            confidence=0.0,
            candidates=[],
            needs_clarification=False,
        )
        mock_search.return_value = []
        mock_should_sum.return_value = False

        async def mock_stream_generator(messages):
            yield "根据之前的讨论"

        mock_stream.return_value = mock_stream_generator([])

        # 执行管线
        events: list[SSEEvent] = []
        async for event in chain.ask("最近有什么新进展？", conversation_id=conversation_id):
            events.append(event)

    # 验证 finish 事件
    finish_events = [e for e in events if e.event == "finish"]
    assert len(finish_events) == 1

    # 验证记忆操作被调用
    mock_get_memory.assert_called_once_with(conversation_id)
    assert mock_add_msg.call_count == 2  # user + assistant
    mock_should_sum.assert_called_once_with(conversation_id)


async def test_rag_chain_retrieval_failure(
    llm_service: LLMService,
    embedding_service: EmbeddingService,
) -> None:
    """检索失败时的容错。"""
    chain = RAGChain(llm_service, embedding_service)

    with patch.object(chain._rewriter, "rewrite", new_callable=AsyncMock) as mock_rewrite, \
         patch.object(chain._classifier, "classify", new_callable=AsyncMock) as mock_classify, \
         patch.object(chain._retriever, "search", new_callable=AsyncMock) as mock_search, \
         patch.object(chain._llm, "stream_chat") as mock_stream:

        mock_rewrite.return_value = RewriteResult(
            rewritten="什么是RAG",
            sub_questions=[],
            normalized_terms={},
        )
        mock_classify.return_value = IntentResult(
            intent=None,
            confidence=0.0,
            candidates=[],
            needs_clarification=False,
        )
        # 检索抛出异常
        mock_search.side_effect = RuntimeError("向量数据库连接失败")

        async def mock_stream_generator(messages):
            yield "抱歉，暂时无法获取相关信息"

        mock_stream.return_value = mock_stream_generator([])

        # 执行管线
        events: list[SSEEvent] = []
        async for event in chain.ask("什么是RAG？"):
            events.append(event)

    # 管线应正常完成（检索失败的容错处理）
    event_types = [e.event for e in events]
    assert "finish" in event_types, "即使检索失败也应返回 finish 事件"
    assert "error" not in event_types, "检索失败应被容错，不应产生 error 事件"


async def test_rag_chain_llm_failure(
    llm_service: LLMService,
    embedding_service: EmbeddingService,
) -> None:
    """LLM 生成失败时的容错。"""
    chain = RAGChain(llm_service, embedding_service)

    with patch.object(chain._rewriter, "rewrite", new_callable=AsyncMock) as mock_rewrite, \
         patch.object(chain._classifier, "classify", new_callable=AsyncMock) as mock_classify, \
         patch.object(chain._retriever, "search", new_callable=AsyncMock) as mock_search, \
         patch.object(chain._llm, "stream_chat") as mock_stream:

        mock_rewrite.return_value = RewriteResult(
            rewritten="什么是RAG",
            sub_questions=[],
            normalized_terms={},
        )
        mock_classify.return_value = IntentResult(
            intent=None,
            confidence=0.0,
            candidates=[],
            needs_clarification=False,
        )
        mock_search.return_value = []

        # LLM 流式生成抛出异常
        async def mock_stream_generator(messages):
            yield "开始生成"
            raise RuntimeError("LLM 服务超时")

        mock_stream.return_value = mock_stream_generator([])

        # 执行管线
        events: list[SSEEvent] = []
        async for event in chain.ask("什么是RAG？"):
            events.append(event)

    # 验证管线能正常结束
    event_types = [e.event for e in events]

    # 应有 error 事件（LLM 生成失败）
    assert "error" in event_types, "LLM 失败应产生 error 事件"
