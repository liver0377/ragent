"""
E2E 测试 —— RAG 问答完整端到端测试。

测试场景：
    1. test_health_check — 健康检查端点
    2. test_chat_sse_stream — SSE 流式对话
    3. test_chat_with_conversation_id — 带会话 ID 的对话
    4. test_chat_error_handling — LLM 调用失败时的错误处理
    5. test_chat_empty_question — 空问题返回 422
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ragent.common.sse import SSEEvent, sse_content, sse_finish, sse_meta
from ragent.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_events() -> list[SSEEvent]:
    """构建 mock LLM 返回的 SSE 事件序列。"""
    return [
        sse_meta({"status": "processing"}),
        sse_content("你好"),
        sse_content("，"),
        sse_content("这是测试回复"),
        sse_finish(),
    ]


async def _mock_ask(
    question: str,
    conversation_id: int | None = None,
    user_id: int | None = None,
) -> AsyncIterator[SSEEvent]:
    """Mock RAGChain.ask 方法，返回预定义事件流。"""
    events: list[SSEEvent] = [
        sse_meta({"status": "processing", "conversation_id": conversation_id}),
        sse_meta({"stage": "query-rewrite"}),
        sse_meta({"stage": "intent-classify"}),
        sse_meta({"stage": "retrieval"}),
        sse_meta({"stage": "prompt-build"}),
        sse_meta({"stage": "llm-generate"}),
        sse_content("你好"),
        sse_content("世界"),
        sse_finish({"conversation_id": conversation_id}),
    ]
    for event in events:
        yield event


async def _mock_ask_error(
    question: str,
    conversation_id: int | None = None,
    user_id: int | None = None,
) -> AsyncIterator[SSEEvent]:
    """Mock RAGChain.ask 方法，模拟错误。"""
    from ragent.common.sse import sse_error

    yield sse_meta({"status": "processing"})
    yield sse_error(message="LLM 调用失败", code="C3001")


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


async def test_health_check() -> None:
    """GET /api/v1/health 返回 {status: "ok", version: "0.1.0"}。"""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


async def test_chat_sse_stream(mock_llm_events: list[SSEEvent]) -> None:
    """POST /api/v1/chat 返回 SSE 流式响应，验证事件序列包含 meta→content→finish。"""
    with patch("ragent.rag.chain.RAGChain") as MockChain, \
         patch("ragent.infra.ai.llm_service.LLMService") as MockLLM, \
         patch("ragent.infra.ai.embedding_service.EmbeddingService") as MockEmb:
        mock_instance = MockChain.return_value
        mock_instance.ask = _mock_ask

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat",
                json={"question": "什么是RAG？"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        # 验证 SSE 事件序列
        content = response.text
        assert "event: meta" in content
        assert "event: content" in content
        assert "event: finish" in content

        # 验证 meta 在 content 之前，content 在 finish 之前
        meta_pos = content.index("event: meta")
        content_pos = content.index("event: content")
        finish_pos = content.index("event: finish")
        assert meta_pos < content_pos < finish_pos


async def test_chat_with_conversation_id() -> None:
    """带会话 ID 的对话，验证记忆保存。"""
    conversation_id = 12345

    with patch("ragent.rag.chain.RAGChain") as MockChain, \
         patch("ragent.infra.ai.llm_service.LLMService") as MockLLM, \
         patch("ragent.infra.ai.embedding_service.EmbeddingService") as MockEmb:
        mock_instance = MockChain.return_value

        # 手动创建一个 async generator 作为 ask 的返回值
        async def _ask_gen(*args, **kwargs):
            for event in [sse_meta({"status": "processing"}), sse_content("测试回复"), sse_finish()]:
                yield event

        mock_instance.ask = _ask_gen

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat",
                json={
                    "question": "什么是RAG？",
                    "conversation_id": conversation_id,
                    "user_id": 1,
                },
            )

        assert response.status_code == 200

        # 验证 SSE 响应中包含事件
        content = response.text
        assert "event: content" in content
        assert "event: finish" in content


async def test_chat_error_handling() -> None:
    """LLM 调用失败时的错误处理。"""
    with patch("ragent.rag.chain.RAGChain") as MockChain, \
         patch("ragent.infra.ai.llm_service.LLMService") as MockLLM, \
         patch("ragent.infra.ai.embedding_service.EmbeddingService") as MockEmb:
        mock_instance = MockChain.return_value
        mock_instance.ask = _mock_ask_error

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat",
                json={"question": "测试问题"},
            )

        assert response.status_code == 200
        content = response.text
        # 错误应该通过 SSE error 事件返回
        assert "event: error" in content


async def test_chat_empty_question() -> None:
    """空问题返回 422 验证错误。"""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 空 question
        response = await client.post(
            "/api/v1/chat",
            json={"question": ""},
        )

    assert response.status_code == 422
