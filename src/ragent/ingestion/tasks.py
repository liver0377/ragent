"""
Celery 任务定义模块 —— 注册所有异步任务。

核心职责：
    1. 定义文档摄入任务 :func:`run_ingestion_pipeline`
    2. 通过 Celery Worker 异步执行管线
    3. 将执行结果写入 Celery Result Backend 供查询
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Any

from ragent.common.celery_app import INGESTION_TASK_QUEUE, get_celery_app
from ragent.ingestion.context import IngestionContext
from ragent.ingestion.pipeline import IngestionPipeline, NodeConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  获取已配置的 Celery 实例
# ---------------------------------------------------------------------------
celery_app = get_celery_app()

# ---------------------------------------------------------------------------
#  默认管线节点配置
# ---------------------------------------------------------------------------

DEFAULT_PIPELINE_NODES: list[dict[str, Any]] = [
    {
        "node_id": "fetcher",
        "node_type": "fetcher",
        "next_node_id": "parser",
    },
    {
        "node_id": "parser",
        "node_type": "parser",
        "next_node_id": "enhancer",
    },
    {
        "node_id": "enhancer",
        "node_type": "enhancer",
        "next_node_id": "chunker",
    },
    {
        "node_id": "chunker",
        "node_type": "chunker",
        "next_node_id": "enricher",
    },
    {
        "node_id": "enricher",
        "node_type": "enricher",
        "next_node_id": "indexer",
    },
    {
        "node_id": "indexer",
        "node_type": "indexer",
        "next_node_id": None,
    },
]


# ---------------------------------------------------------------------------
#  辅助函数：序列化 IngestionContext → dict
# ---------------------------------------------------------------------------

async def _update_doc_chunk_count_async(source_location: str, chunk_count: int) -> None:
    """回写 chunk_count 到 t_knowledge_document 表。

    使用 asyncpg 异步连接，在管线执行的同一事件循环中运行。

    Args:
        source_location: 文件路径（如 /data/pdfs/304629849111134208_xxx.pdf）。
        chunk_count: 分块数量。
    """
    import asyncpg as _asyncpg

    from ragent.config.settings import get_settings

    settings = get_settings()
    db_url: str = getattr(settings, "DATABASE_URL", "")

    conn = await _asyncpg.connect(db_url)
    try:
        await conn.execute(
            "UPDATE t_knowledge_document SET chunk_count = $1 WHERE file_url = $2",
            chunk_count, source_location,
        )
        logger.info(
            "已回写 chunk_count=%d, file_url=%s", chunk_count, source_location,
        )
    finally:
        await conn.close()


def _serialize_context(ctx: IngestionContext) -> dict[str, Any]:
    """将 IngestionContext 序列化为可 JSON 化的字典。

    Args:
        ctx: 管线执行上下文。

    Returns:
        可 JSON 序列化的字典。
    """
    chunks_data = []
    for chunk in ctx.chunks:
        chunk_dict = {
            "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
            "index": chunk.index,
            "char_count": chunk.char_count,
            "token_count": chunk.token_count,
            "content_hash": chunk.content_hash,
            "keywords": chunk.keywords,
            "summary": chunk.summary,
            "metadata": chunk.metadata,
        }
        chunks_data.append(chunk_dict)

    return {
        "task_id": ctx.task_id,
        "pipeline_id": ctx.pipeline_id,
        "source_type": ctx.source_type,
        "source_location": ctx.source_location,
        "file_type": ctx.file_type,
        "text_length": len(ctx.plain_text) if ctx.plain_text else 0,
        "keywords": ctx.keywords,
        "chunk_count": len(ctx.chunks),
        "chunks": chunks_data,
        "metadata": ctx.metadata,
        "status": ctx.status,
        "error_message": ctx.error_message,
    }


# ---------------------------------------------------------------------------
#  Celery 任务：文档摄入管线
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="ragent.ingestion.run_pipeline",
    queue=INGESTION_TASK_QUEUE,
    acks_late=True,
    track_started=True,
    time_limit=600,       # 硬超时 10 分钟
    soft_time_limit=540,  # 软超时 9 分钟
)
def run_ingestion_pipeline(
    self: Any,
    task_id: int,
    pipeline_id: int,
    source_type: str,
    source_location: str,
    pipeline_nodes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """异步执行文档摄入管线。

    由 Celery Worker 进程调用，通过 asyncio.run() 驱动异步管线执行。

    Args:
        self:          Celery 任务实例（bind=True）。
        task_id:       Snowflake 任务 ID。
        pipeline_id:   管线配置 ID。
        source_type:   来源类型（local/http/s3）。
        source_location: 文件路径或 URL。
        pipeline_nodes: 自定义管线节点配置列表（可选，默认使用 DEFAULT_PIPELINE_NODES）。

    Returns:
        序列化后的 IngestionContext 字典，包含执行状态、分块结果等。
    """
    logger.info(
        "Celery 任务开始: task_id=%s, source_type=%s, source_location=%s",
        task_id, source_type, source_location,
    )

    # 更新任务状态：STARTED
    self.update_state(
        state="PROCESSING",
        meta={"stage": "初始化", "progress": 0},
    )

    # 构建管线节点配置
    nodes_config = pipeline_nodes or DEFAULT_PIPELINE_NODES
    nodes = [NodeConfig(**nc) for nc in nodes_config]

    # 创建管线
    pipeline = IngestionPipeline(nodes)
    pipeline.validate()

    # 创建上下文
    ctx = IngestionContext(
        task_id=task_id,
        pipeline_id=pipeline_id,
        source_type=source_type,
        source_location=source_location,
    )

    # 用 asyncio.run 驱动异步管线
    try:
        start_time = time.monotonic()

        # 更新状态：处理中
        self.update_state(
            state="PROCESSING",
            meta={"stage": "管线执行中", "progress": 20},
        )

        async def _run_pipeline_and_update() -> None:
            """执行管线并回写 chunk_count 到数据库。"""
            nonlocal ctx
            ctx = await pipeline.execute(ctx)

            # 回写 chunk_count 到 t_knowledge_document 表
            chunk_count = len(ctx.chunks)
            if chunk_count > 0:
                try:
                    await _update_doc_chunk_count_async(source_location, chunk_count)
                except Exception as db_exc:
                    logger.warning("回写 chunk_count 失败: %s", db_exc)

        ctx = asyncio.run(_run_pipeline_and_update())

        elapsed = (time.monotonic() - start_time) * 1000

        logger.info(
            "Celery 任务完成: task_id=%s, status=%s, chunks=%d, elapsed=%.0fms",
            task_id, ctx.status, len(ctx.chunks), elapsed,
        )

        result = _serialize_context(ctx)
        result["elapsed_ms"] = elapsed

        return result

    except Exception as exc:
        logger.error(
            "Celery 任务失败: task_id=%s, error=%s",
            task_id, exc,
        )
        ctx.mark_failed(str(exc))
        return _serialize_context(ctx)
