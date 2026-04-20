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

from fastapi import APIRouter, File, Form, Query, UploadFile
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
    knowledge_base_id: int | None = Field(default=None, description="知识库 ID（可选，指定检索范围）")
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
async def chat(
    request: ChatRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    """RAG 问答接口 —— 流式 SSE 响应（需认证）。"""
    logger.info(
        "收到问答请求 | user=%s | question='%s' | conv_id=%s",
        current_user.username, request.question[:50], request.conversation_id,
    )

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
        user_id=current_user.id,
        db_session=db,
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
    knowledge_base_id: int = Form(..., description="目标知识库 ID"),
    files: list[UploadFile] = File(..., description="上传的文件（支持多文件）"),
    db: DbSession = None,
    current_user: CurrentUser = None,
) -> Result[Any]:
    """批量上传文档到知识库（multipart/form-data），提交异步摄入任务（需认证）。

    流程（每个文件）：
        1. 验证知识库存在
        2. 将文件保存到 data/pdfs/ 目录
        3. 创建文档记录
        4. 通过 Celery 投递异步摄入任务

    Args:
        knowledge_base_id: 目标知识库 ID（表单字段）。
        files: 上传的文件列表。
        db: 异步数据库会话。
        current_user: 当前登录用户。

    Returns:
        Result 包含每个文件的 task_id 和状态。
    """
    import os
    import shutil

    from ragent.ingestion.tasks import run_ingestion_pipeline

    # 解析 db 和 current_user（FastAPI 注入的参数会被自动解析）
    if db is None or current_user is None:
        return Result.error(code=500, message="服务内部错误")

    # 验证知识库存在
    kb_result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
    )
    kb = kb_result.scalar_one_or_none()
    if kb is None:
        return Result.error(code=404, message=f"知识库 {knowledge_base_id} 不存在")

    # 确保存储目录存在
    upload_dir = "/app/data/pdfs"
    os.makedirs(upload_dir, exist_ok=True)

    results = []
    for upload_file in files:
        filename = upload_file.filename or "unknown"
        file_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"

        # 检查文件类型
        allowed_extensions = {"pdf", "txt", "md", "docx", "doc", "csv", "xlsx", "json"}
        if file_ext not in allowed_extensions:
            results.append({
                "filename": filename,
                "status": "REJECTED",
                "message": f"不支持的文件类型: {file_ext}",
            })
            continue

        # 保存文件到磁盘
        file_path = os.path.join(upload_dir, filename)
        with open(file_path, "wb") as f:
            content = await upload_file.read()
            f.write(content)

        # 创建文档记录
        doc_id = generate_id()
        doc = KnowledgeDocument(
            id=doc_id,
            kb_id=knowledge_base_id,
            doc_name=filename,
            file_url=f"/data/pdfs/{filename}",
            file_type=file_ext,
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
            pipeline_id=knowledge_base_id,
            source_type="local",
            source_location=filename,
        )

        logger.info(
            "文档上传: doc_id=%s, task_id=%s, celery_id=%s, kb=%s, file=%s, user=%s, size=%d",
            doc_id, task_id, celery_result.id, knowledge_base_id, filename,
            current_user.username, len(content),
        )

        results.append({
            "filename": filename,
            "doc_id": doc_id,
            "task_id": task_id,
            "celery_task_id": celery_result.id,
            "status": "PENDING",
            "message": f"文档 {filename} 已提交摄入队列",
            "file_size": len(content),
        })

    success_count = sum(1 for r in results if r["status"] == "PENDING")
    fail_count = len(results) - success_count

    return Result.success(data={
        "total": len(results),
        "success": success_count,
        "failed": fail_count,
        "details": results,
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


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------


class ConversationCreateRequest(BaseModel):
    """创建会话请求体。"""

    title: str = Field(default="新对话", max_length=200, description="会话标题")


@router.post("/conversations", summary="创建会话")
async def create_conversation(
    request: ConversationCreateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> Result[Any]:
    """创建新会话（需认证）。"""
    from ragent.common.models import Conversation as Conv

    conv_id = generate_id()
    conv = Conv(
        id=conv_id,
        user_id=current_user.id,
        title=request.title,
    )
    db.add(conv)
    await db.flush()

    return Result.success(data={
        "id": conv.id,
        "title": conv.title,
        "user_id": conv.user_id,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
    })


@router.get("/conversations", summary="会话列表")
async def list_conversations(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Result[Any]:
    """获取当前用户的会话列表（需认证，按最后消息时间倒序）。"""
    from ragent.common.models import Conversation as Conv

    # 只返回当前用户的会话
    base_query = select(Conv).where(Conv.user_id == current_user.id)

    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(
        base_query.order_by(Conv.last_message_time.desc().nullsfirst(), Conv.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    convs = result.scalars().all()

    items = [
        {
            "id": c.id,
            "title": c.title,
            "last_message_time": c.last_message_time.isoformat() if c.last_message_time else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in convs
    ]

    return Result.success(data={
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/conversations/{conv_id}", summary="会话详情")
async def get_conversation(
    conv_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> Result[Any]:
    """获取会话详情及其消息列表（需认证，只能看自己的）。"""
    from ragent.common.models import Conversation as Conv

    result = await db.execute(
        select(Conv).where(Conv.id == conv_id, Conv.user_id == current_user.id)
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        return Result.error(code=404, message="会话不存在或无权访问")

    # 获取消息
    from ragent.common.models import Message as Msg
    msg_result = await db.execute(
        select(Msg)
        .where(Msg.conversation_id == conv_id)
        .order_by(Msg.created_at.asc())
    )
    messages = msg_result.scalars().all()

    msg_items = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]

    return Result.success(data={
        "id": conv.id,
        "title": conv.title,
        "user_id": conv.user_id,
        "last_message_time": conv.last_message_time.isoformat() if conv.last_message_time else None,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "messages": msg_items,
    })


@router.delete("/conversations/{conv_id}", summary="删除会话")
async def delete_conversation(
    conv_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> Result[Any]:
    """删除会话及其所有消息（需认证，只能删自己的）。"""
    from ragent.common.models import Conversation as Conv

    result = await db.execute(
        select(Conv).where(Conv.id == conv_id, Conv.user_id == current_user.id)
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        return Result.error(code=404, message="会话不存在或无权访问")

    await db.delete(conv)

    logger.info("会话删除: conv_id=%s, user=%s", conv_id, current_user.username)
    return Result.success(data={"message": "会话已删除"})
