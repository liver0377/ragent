"""Celery 异步任务队列模块。

使用 Celery + Redis 作为消息代理，用于处理异步文档摄入任务和反馈处理。
通过懒加载方式从 settings 中读取 CELERY_BROKER_URL 和 CELERY_RESULT_BACKEND，
避免模块导入时产生配置依赖。
"""

from __future__ import annotations

import logging

from celery import Celery

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  队列名称常量
# ---------------------------------------------------------------------------

#: 文档摄入任务队列 — 用于整体文档摄入任务
INGESTION_TASK_QUEUE: str = "ingestion.task"

#: 文档分块队列 — 用于文档分块处理任务
INGESTION_CHUNK_QUEUE: str = "ingestion.chunk"

#: RAG 反馈队列 — 用于用户反馈收集与处理
RAG_FEEDBACK_QUEUE: str = "rag.feedback"

# ---------------------------------------------------------------------------
#  模块级 Celery 实例（延迟配置）
# ---------------------------------------------------------------------------

celery_app: Celery = Celery("ragent")


def _configure_app(app: Celery) -> None:
    """根据全局配置设置 Celery 实例的 broker 和 backend。

    从 ``ragent.config.settings.get_settings()`` 读取连接信息，
    并通过 ``conf.update`` 应用全部运行参数。

    Args:
        app: 需要配置的 Celery 实例。
    """
    from ragent.config.settings import get_settings

    settings = get_settings()

    broker_url: str = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/1")
    result_backend: str = getattr(
        settings, "CELERY_RESULT_BACKEND", "redis://localhost:6379/2"
    )

    app.conf.update(
        # ---- 连接配置 ----
        broker_url=broker_url,
        result_backend=result_backend,
        # ---- 序列化 ----
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # ---- 任务行为 ----
        task_track_started=True,
        task_acks_late=True,
        # ---- Worker 行为 ----
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        # ---- 时区 ----
        timezone="Asia/Shanghai",
        enable_utc=True,
    )

    # 自动发现各模块下的 tasks.py
    app.autodiscover_tasks(["ragent.ingestion"])

    logger.info(
        "Celery 应用已配置: broker=%s, backend=%s",
        broker_url,
        result_backend,
    )

_configured: bool = False


def get_celery_app() -> Celery:
    """获取已配置的 Celery 应用实例。

    首次调用时从 settings 读取 broker / backend 等配置并应用到全局实例，
    后续调用直接返回已配置的实例（幂等）。

    Returns:
        Celery: 已完成配置的 Celery 应用实例。
    """
    global _configured

    if not _configured:
        _configure_app(celery_app)
        _configured = True

    return celery_app
