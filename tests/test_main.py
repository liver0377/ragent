"""
L4 应用层测试 —— FastAPI 应用入口、中间件、路由集成测试。

测试覆盖：
    - 应用创建
    - 健康检查端点
    - trace_id 中间件注入
    - 异常处理中间件返回统一格式
    - 聊天端点结构（Mock RAGChain）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ragent.common.sse import SSEEvent, sse_content, sse_finish, sse_meta
from ragent.main import app, create_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """创建 FastAPI 测试客户端。

    Returns:
        TestClient 实例。
    """
    return TestClient(app)


@pytest.fixture
def fresh_app() -> TestClient:
    """创建全新应用实例的测试客户端。

    Returns:
        基于新创建 app 的 TestClient。
    """
    new_app = create_app()
    return TestClient(new_app)


# ---------------------------------------------------------------------------
# 应用创建测试
# ---------------------------------------------------------------------------


class TestAppCreation:
    """应用创建相关测试。"""

    def test_create_app_returns_fastapi_instance(self) -> None:
        """create_app() 应返回正确配置的 FastAPI 实例。"""
        application = create_app()
        assert application.title == "Ragent API"
        assert application.version == "0.1.0"

    def test_app_has_routes(self) -> None:
        """应用应包含注册的路由。"""
        application = create_app()
        paths = [route.path for route in application.routes if hasattr(route, "path")]
        assert "/api/v1/health" in paths
        assert "/api/v1/chat" in paths
        assert "/api/v1/knowledge-bases" in paths
        assert "/api/v1/documents/upload" in paths
        assert "/api/v1/ingestion/tasks/{task_id}" in paths

    def test_app_importable(self) -> None:
        """模块级 app 实例应可直接导入。"""
        from ragent.main import app as imported_app

        assert imported_app is not None
        assert imported_app.title == "Ragent API"


# ---------------------------------------------------------------------------
# 健康检查测试
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """健康检查端点测试。"""

    def test_health_check_returns_ok(self, client: TestClient) -> None:
        """GET /api/v1/health 应返回 200 和 status=ok。"""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data: dict[str, Any] = response.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_check_version_matches_settings(self, client: TestClient) -> None:
        """健康检查返回的版本号应与配置一致。"""
        response = client.get("/api/v1/health")
        data = response.json()
        assert data["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# 中间件测试
# ---------------------------------------------------------------------------


class TestTraceMiddleware:
    """链路追踪中间件测试。"""

    def test_trace_id_in_response_headers(self, client: TestClient) -> None:
        """响应应包含 X-Trace-Id 头。"""
        response = client.get("/api/v1/health")
        assert "X-Trace-Id" in response.headers
        assert len(response.headers["X-Trace-Id"]) > 0

    def test_trace_id_propagation(self, client: TestClient) -> None:
        """若请求携带 X-Trace-Id，响应应复用该值。"""
        custom_trace = "my-custom-trace-12345"
        response = client.get("/api/v1/health", headers={"X-Trace-Id": custom_trace})
        assert response.headers["X-Trace-Id"] == custom_trace

    def test_trace_id_auto_generated(self, client: TestClient) -> None:
        """未携带 X-Trace-Id 时，应自动生成 UUID hex。"""
        response = client.get("/api/v1/health")
        trace_id: str = response.headers["X-Trace-Id"]
        # UUID hex 长度为 32
        assert len(trace_id) == 32


class TestRequestContextMiddleware:
    """请求上下文中间件测试。"""

    def test_user_context_from_headers(self, client: TestClient) -> None:
        """中间件应能提取请求头中的用户信息。"""
        # 使用健康检查端点验证中间件不阻塞请求
        response = client.get(
            "/api/v1/health",
            headers={
                "X-User-Id": "u-001",
                "X-Username": "testuser",
                "X-User-Role": "admin",
            },
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 异常处理中间件测试
# ---------------------------------------------------------------------------


class TestExceptionHandlerMiddleware:
    """全局异常处理中间件测试。"""

    def test_exception_returns_error_format(self, client: TestClient) -> None:
        """未处理异常应返回 Result.error() 格式。"""
        # 通过添加一个临时异常路由来测试中间件
        from fastapi import FastAPI
        from starlette.testclient import TestClient as _TC

        from ragent.app.middleware import ExceptionHandlerMiddleware

        test_app = FastAPI()
        test_app.add_middleware(ExceptionHandlerMiddleware)

        @test_app.get("/fail")
        async def fail_endpoint() -> None:
            """故意抛出异常的端点。"""
            raise RuntimeError("测试异常")

        test_client = _TC(test_app)
        response = test_client.get("/fail")
        assert response.status_code == 500
        data: dict[str, Any] = response.json()
        assert data["code"] == 500
        assert data["message"] == "内部服务错误"
        assert data["data"] is None


# ---------------------------------------------------------------------------
# 聊天端点测试
# ---------------------------------------------------------------------------


class TestChatEndpoint:
    """RAG 问答端点测试。"""

    def test_chat_returns_sse_stream(self, client: TestClient) -> None:
        """POST /api/v1/chat 应返回 SSE 流式响应。"""
        # Mock RAGChain.ask 返回简单事件流
        mock_events: list[SSEEvent] = [
            sse_meta({"status": "processing"}),
            sse_content("你好"),
            sse_finish(),
        ]

        async def mock_ask(
            question: str,
            conversation_id: int | None = None,
            user_id: int | None = None,
        ) -> AsyncIterator[SSEEvent]:
            for event in mock_events:
                yield event

        # Patch 在源模块层级，因为路由函数内部延迟导入
        with patch("ragent.rag.chain.RAGChain") as MockChain, \
             patch("ragent.infra.ai.llm_service.LLMService") as MockLLM, \
             patch("ragent.infra.ai.embedding_service.EmbeddingService") as MockEmb:
            mock_instance = MockChain.return_value
            mock_instance.ask = mock_ask

            response = client.post(
                "/api/v1/chat",
                json={"question": "测试问题"},
            )
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            # 验证 SSE 内容
            content: str = response.text
            assert "event: meta" in content
            assert "event: content" in content
            assert "event: finish" in content

    def test_chat_without_question_fails(self, client: TestClient) -> None:
        """POST /api/v1/chat 不带 question 应返回 422。"""
        response = client.post("/api/v1/chat", json={})
        assert response.status_code == 422

    def test_chat_empty_question_fails(self, client: TestClient) -> None:
        """POST /api/v1/chat 空 question 应返回 422。"""
        response = client.post("/api/v1/chat", json={"question": ""})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 桩端点测试
# ---------------------------------------------------------------------------


class TestStubEndpoints:
    """桩实现端点测试。"""

    def test_create_knowledge_base_stub(self, client: TestClient) -> None:
        """创建知识库桩应返回 200 和 501 错误码。"""
        response = client.post(
            "/api/v1/knowledge-bases",
            json={"name": "测试知识库", "description": "描述"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 501

    def test_list_knowledge_bases_stub(self, client: TestClient) -> None:
        """知识库列表桩应返回 200 和 501 错误码。"""
        response = client.get("/api/v1/knowledge-bases")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 501

    def test_upload_document_stub(self, client: TestClient) -> None:
        """文档上传桩应返回 200 和 501 错误码。"""
        response = client.post(
            "/api/v1/documents/upload",
            json={"knowledge_base_id": 1, "filename": "test.pdf"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 501

    def test_ingestion_task_status_stub(self, client: TestClient) -> None:
        """入库任务状态查询桩应返回 200 和 501 错误码。"""
        response = client.get("/api/v1/ingestion/tasks/task-123")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 501
