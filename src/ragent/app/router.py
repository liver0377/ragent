"""
API 路由模块 —— 定义所有 HTTP 接口

提供以下端点：
    - ``GET  /api/v1/health``                    —— 健康检查
    - ``POST /api/v1/chat``                      —— RAG 问答（SSE 流式响应）
    - ``POST /api/v1/knowledge-bases``            —— 创建知识库
    - ``GET  /api/v1/knowledge-bases``            —— 知识库列表
    - ``GET  /api/v1/knowledge-bases/{kb_id}``    —— 知识库详情
    - ``DELETE /api/v1/knowledge-bases/{kb_id}``  —— 删除知识库
    - ``POST /api/v1/documents/upload``           —— 文档上传 → Celery 异步摄入
    - ``GET  /api/v1/ingestion/tasks/{task_id}``  —— 查询入库任务状态
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.responses import StreamingResponse

from ragent.app.deps import CurrentUser, DbSession
from ragent.common.logging import get_logger
from ragent.common.models import KnowledgeBase, KnowledgeDocument
from ragent.common.response import Result
from ragent.common.snowflake import generate_id
from ragent.common.sse import SSEEvent, create_sse_response, sse_content, sse_finish, sse_meta
from ragent.config.settings import get_settings

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """RAG 问答请求体。"""

    question: str = Field(..., min_length=1, description="用户问题")
    conversation_id: int | None = Field(default=None, description="会话 ID")
    user_id: int | None = Field(default=None, description="用户 ID")


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求体。"""

    name: str = Field(..., min_length=1, max_length=100, description="知识库名称")
    description: str = Field(default="", max_length=500, description="知识库描述")


class KnowledgeBaseUpdateRequest(BaseModel):
    """更新知识库请求体。"""

    name: str | None = Field(default=None, min_length=1, max_length=100, description="知识库名称")
    description: str | None = Field(default=None, max_length=500, description="知识库描述")


class DocumentUploadRequest(BaseModel):
    """文档上传请求体（元数据部分）。"""

    knowledge_base_id: int = Field(..., description="目标知识库 ID")
    filename: str = Field(..., min_length=1, description="文件名")


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """健康检查端点。"""
    settings = get_settings()
    return {"status": "ok", "version": settings.APP_VERSION}


# ---------------------------------------------------------------------------
# RAG 问答 —— SSE 流式响应
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """RAG 问答接口 —— 流式 SSE 响应。"""
    logger.info("收到问答请求 | question='%s' | conv_id=%s", request.question[:50], request.conversation_id)

    from ragent.infra.ai.embedding_service import EmbeddingService
    from ragent.infra.ai.llm_service import LLMService
    from ragent.infra.ai.models import ModelConfigManager
    from ragent.infra.ai.model_selector import ModelSelector
    from ragent.rag.chain import RAGChain

    settings = get_settings()

    config_manager = ModelConfigManager()
    selector = ModelSelector(config_manager)
    llm_service = LLMService(config_manager, selector)
    embedding_service = EmbeddingService(config_manager, selector)
    chain = RAGChain(llm_service, embedding_service)

    events = chain.ask(
        question=request.question,
        conversation_id=request.conversation_id,
        user_id=request.user_id,
    )

    return create_sse_response(events)


# ---------------------------------------------------------------------------
# 知识库管理
# ---------------------------------------------------------------------------


@router.post("/knowledge-bases", summary="创建知识库")
async def create_knowledge_base(
    request: KnowledgeBaseCreateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> Result[Any]:
    """创建知识库（需认证）。

    流程：
        1. 生成 Snowflake ID
        2. 创建知识库记录
        3. 返回知识库信息

    Args:
        request: 知识库创建请求体。
        db: 异步数据库会话。
        current_user: 当前登录用户。

    Returns:
        Result 包含新创建的知识库信息。
    """
    settings = get_settings()
    kb_id = generate_id()

    # 使用默认 embedding 模型和自动生成的 collection 名称
    collection_name = f"kb_{kb_id}"

    kb = KnowledgeBase(
        id=kb_id,
        name=request.name,
        description=request.description,
        embedding_model=settings.EMBEDDING_MODEL,
        collection_name=collection_name,
    )
    db.add(kb)
    await db.flush()

    logger.info("知识库创建成功: id=%s, name=%s, user=%s", kb_id, request.name, current_user.username)

    return Result.success(data={
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "embedding_model": kb.embedding_model,
        "collection_name": kb.collection_name,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    })


@router.get("/knowledge-bases", summary="知识库列表")
async def list_knowledge_bases(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
) -> Result[Any]:
    """获取知识库列表（需认证，分页）。

    Args:
        db: 异步数据库会话。
        current_user: 当前登录用户。
        page: 页码（从 1 开始）。
        page_size: 每页数量。

    Returns:
        Result 包含知识库列表和分页信息。
    """
    # 查询总数
    count_result = await db.execute(select(func.count()).select_from(KnowledgeBase))
    total = count_result.scalar() or 0

    # 分页查询
    offset = (page - 1) * page_size
    result = await db.execute(
        select(KnowledgeBase)
        .order_by(KnowledgeBase.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    kbs = result.scalars().all()

    items = [
        {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "embedding_model": kb.embedding_model,
            "collection_name": kb.collection_name,
            "created_at": kb.created_at.isoformat() if kb.created_at else None,
            "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
        }
        for kb in kbs
    ]

    return Result.success(data={
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/knowledge-bases/{kb_id}", summary="知识库详情")
async def get_knowledge_base(
    kb_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> Result[Any]:
    """获取知识库详情（需认证）。

    Args:
        kb_id: 知识库 ID。
        db: 异步数据库会话。
        current_user: 当前登录用户。

    Returns:
        Result 包含知识库详情。
    """
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    )
    kb = result.scalar_one_or_none()

    if kb is None:
        return Result.error(code=404, message=f"知识库 {kb_id} 不存在")

    # 查询关联的文档数量
    doc_count_result = await db.execute(
        select(func.count())
        .select_from(KnowledgeDocument)
        .where(KnowledgeDocument.kb_id == kb_id)
    )
    doc_count = doc_count_result.scalar() or 0

    return Result.success(data={
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "embedding_model": kb.embedding_model,
        "collection_name": kb.collection_name,
        "document_count": doc_count,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
    })


@router.delete("/knowledge-bases/{kb_id}", summary="删除知识库")
async def delete_knowledge_base(
    kb_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> Result[Any]:
    """删除知识库（需认证）。

    同时删除关联的文档和分块记录。

    Args:
        kb_id: 知识库 ID。
        db: 异步数据库会话。
        current_user: 当前登录用户。

    Returns:
        Result 成功响应。
    """
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    )
    kb = result.scalar_one_or_none()

    if kb is None:
        return Result.error(code=404, message=f"知识库 {kb_id} 不存在")

    await db.delete(kb)

    logger.info("知识库删除成功: id=%s, name=%s, user=%s", kb_id, kb.name, current_user.username)

    return Result.success(data={"message": f"知识库 '{kb.name}' 已删除"})


# ---------------------------------------------------------------------------
# 文档入库
# ---------------------------------------------------------------------------


@router.post("/documents/upload")
async def upload_document(
    request: DocumentUploadRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> Result[Any]:
    """上传文档到知识库，提交异步摄入任务（需认证）。

    流程：
        1. 验证知识库存在
        2. 创建文档记录
        3. 生成 Snowflake task_id
        4. 通过 Celery 投递异步摄入任务

    Args:
        request: 文档上传请求体。
        db: 异步数据库会话。
        current_user: 当前登录用户。

    Returns:
        Result 包含 task_id 和状态。
    """
    from ragent.ingestion.tasks import run_ingestion_pipeline

    # 验证知识库存在
    kb_result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == request.knowledge_base_id)
    )
    kb = kb_result.scalar_one_or_none()
    if kb is None:
        return Result.error(code=404, message=f"知识库 {request.knowledge_base_id} 不存在")

    # 创建文档记录
    doc_id = generate_id()
    doc = KnowledgeDocument(
        id=doc_id,
        kb_id=request.knowledge_base_id,
        doc_name=request.filename,
        file_url=f"/data/pdfs/{request.filename}",
        file_type=request.filename.rsplit(".", 1)[-1].lower() if "." in request.filename else "unknown",
        enabled=True,
        chunk_count=0,
        chunk_strategy="fixed",
        pipeline_id=None,
        process_mode="auto",
    )
    db.add(doc)
    await db.flush()

    # 提交 Celery 异步任务
    task_id = generate_id()
    celery_result = run_ingestion_pipeline.delay(
        task_id=task_id,
        pipeline_id=request.knowledge_base_id,
        source_type="local",
        source_location=request.filename,
    )

    logger.info(
        "文档上传: doc_id=%s, task_id=%s, celery_id=%s, kb=%s, file=%s, user=%s",
        doc_id, task_id, celery_result.id, request.knowledge_base_id, request.filename, current_user.username,
    )

    return Result.success(data={
        "doc_id": doc_id,
        "task_id": task_id,
        "celery_task_id": celery_result.id,
        "status": "PENDING",
        "message": f"文档 {request.filename} 已提交摄入队列",
    })


@router.get("/ingestion/tasks/{task_id}")
async def get_ingestion_task_status(task_id: str) -> Result[Any]:
    """查询文档入库任务状态。"""
    from celery.result import AsyncResult
    from ragent.ingestion.tasks import celery_app as _app

    result = AsyncResult(task_id, app=_app)

    if result.state == "PENDING":
        return Result.success(data={
            "task_id": task_id,
            "status": "PENDING",
            "message": "任务等待执行",
        })

    if result.state == "PROCESSING":
        meta = result.info or {}
        return Result.success(data={
            "task_id": task_id,
            "status": "PROCESSING",
            "stage": meta.get("stage", ""),
            "progress": meta.get("progress", 0),
        })

    if result.state == "SUCCESS":
        task_result = result.result or {}
        return Result.success(data={
            "task_id": task_id,
            "status": task_result.get("status", "COMPLETED"),
            "chunk_count": task_result.get("chunk_count", 0),
            "file_type": task_result.get("file_type"),
            "text_length": task_result.get("text_length", 0),
            "keywords": task_result.get("keywords", []),
            "elapsed_ms": task_result.get("elapsed_ms"),
            "error_message": task_result.get("error_message"),
        })

    if result.state == "FAILURE":
        return Result.error(
            code=500,
            message=f"任务执行失败: {str(result.result)}",
        )

    return Result.success(data={
        "task_id": task_id,
        "status": result.state,
        "info": str(result.info) if result.info else None,
    })
