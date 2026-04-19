"""
API 路由模块 —— 定义所有 HTTP 接口

提供以下端点：
    - ``GET  /api/v1/health``                    —— 健康检查
    - ``POST /api/v1/chat``                      —— RAG 问答（SSE 流式响应）
    - ``POST /api/v1/knowledge-bases``            —— 创建知识库（桩）
    - ``GET  /api/v1/knowledge-bases``            —— 知识库列表（桩）
    - ``POST /api/v1/documents/upload``           —— 文档上传（桩）
    - ``GET  /api/v1/ingestion/tasks/{task_id}``  —— 入库任务状态（桩）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from ragent.common.logging import get_logger
from ragent.common.response import Result
from ragent.common.sse import SSEEvent, create_sse_response, sse_content, sse_finish, sse_meta
from ragent.config.settings import get_settings

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """RAG 问答请求体。

    Attributes:
        question:        用户问题文本。
        conversation_id: 会话 ID，传入时自动加载历史记忆。
        user_id:         用户 ID，用于限流和上下文隔离。
    """

    question: str = Field(..., min_length=1, description="用户问题")
    conversation_id: int | None = Field(default=None, description="会话 ID")
    user_id: int | None = Field(default=None, description="用户 ID")


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求体。

    Attributes:
        name:        知识库名称。
        description: 知识库描述。
    """

    name: str = Field(..., min_length=1, description="知识库名称")
    description: str = Field(default="", description="知识库描述")


class DocumentUploadRequest(BaseModel):
    """文档上传请求体（元数据部分）。

    Attributes:
        knowledge_base_id: 目标知识库 ID。
        filename:          文件名。
    """

    knowledge_base_id: int = Field(..., description="目标知识库 ID")
    filename: str = Field(..., min_length=1, description="文件名")


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """健康检查端点。

    返回服务状态和版本信息，供负载均衡器和监控探针使用。

    Returns:
        包含 status 和 version 的字典。
    """
    settings = get_settings()
    return {"status": "ok", "version": settings.APP_VERSION}


# ---------------------------------------------------------------------------
# RAG 问答 —— SSE 流式响应
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """RAG 问答接口 —— 流式 SSE 响应。

    接收用户问题，通过 RAG 管线处理，以 SSE 事件流返回结果。

    SSE 事件类型：
        - ``meta``    处理阶段元信息
        - ``thinking`` AI 思考过程
        - ``content``  生成内容片段
        - ``error``    错误信息
        - ``finish``   流结束标记

    Args:
        request: ChatRequest 请求体。

    Returns:
        StreamingResponse（text/event-stream）。
    """
    logger.info("收到问答请求 | question='%s' | conv_id=%s", request.question[:50], request.conversation_id)

    # 延迟导入以避免循环依赖
    from ragent.infra.ai.embedding_service import EmbeddingService
    from ragent.infra.ai.llm_service import LLMService
    from ragent.infra.ai.models import ModelConfigManager
    from ragent.infra.ai.model_selector import ModelSelector
    from ragent.rag.chain import RAGChain

    settings = get_settings()

    # 构建依赖链：ModelConfigManager → ModelSelector → LLMService / EmbeddingService → RAGChain
    config_manager = ModelConfigManager()
    selector = ModelSelector(config_manager)
    llm_service = LLMService(config_manager, selector)
    embedding_service = EmbeddingService(config_manager, selector)
    chain = RAGChain(llm_service, embedding_service)

    # 获取异步事件迭代器
    events = chain.ask(
        question=request.question,
        conversation_id=request.conversation_id,
        user_id=request.user_id,
    )

    return create_sse_response(events)


# ---------------------------------------------------------------------------
# 知识库管理（桩实现）
# ---------------------------------------------------------------------------


@router.post("/knowledge-bases")
async def create_knowledge_base(request: KnowledgeBaseCreateRequest) -> Result[Any]:
    """创建知识库。

    .. note:: 当前为桩实现，尚未对接持久化层。

    Args:
        request: 知识库创建请求体。

    Returns:
        Result 错误响应（功能未实现）。
    """
    return Result.error(code=501, message="知识库创建功能尚未实现")


@router.get("/knowledge-bases")
async def list_knowledge_bases() -> Result[Any]:
    """获取知识库列表。

    .. note:: 当前为桩实现，尚未对接持久化层。

    Returns:
        Result 错误响应（功能未实现）。
    """
    return Result.error(code=501, message="知识库列表功能尚未实现")


# ---------------------------------------------------------------------------
# 文档入库（桩实现）
# ---------------------------------------------------------------------------


@router.post("/documents/upload")
async def upload_document(request: DocumentUploadRequest) -> Result[Any]:
    """上传文档到知识库。

    .. note:: 当前为桩实现，尚未对接文件存储和解析管线。

    Args:
        request: 文档上传请求体。

    Returns:
        Result 错误响应（功能未实现）。
    """
    return Result.error(code=501, message="文档上传功能尚未实现")


@router.get("/ingestion/tasks/{task_id}")
async def get_ingestion_task_status(task_id: str) -> Result[Any]:
    """查询文档入库任务状态。

    .. note:: 当前为桩实现，尚未对接任务队列。

    Args:
        task_id: 入库任务 ID。

    Returns:
        Result 错误响应（功能未实现）。
    """
    return Result.error(code=501, message=f"入库任务 {task_id} 查询功能尚未实现")
