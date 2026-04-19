"""
应用入口模块 —— FastAPI 应用创建与生命周期管理。

提供 ``create_app()`` 工厂函数，负责：
    1. 应用生命周期管理（日志初始化、Redis 连接池等）
    2. 中间件注册（链路追踪、上下文注入、异常处理）
    3. 路由挂载

启动方式::

    uvicorn ragent.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from ragent.app.middleware import (
    ExceptionHandlerMiddleware,
    RequestContextMiddleware,
    TraceMiddleware,
)
from ragent.app.router import router
from ragent.common.logging import get_logger, setup_logging
from ragent.config.settings import get_settings

logger: logging.Logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 应用生命周期
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期管理 —— 启动和关闭时的资源初始化/清理。

    启动阶段：
        1. 初始化结构化日志
        2. 尝试初始化 Redis 连接池（失败不阻塞启动）
        3. 输出启动日志

    关闭阶段：
        1. 关闭 Redis 连接池
        2. 输出关闭日志

    Args:
        app: FastAPI 应用实例。
    """
    settings = get_settings()

    # ---- 启动 ----
    setup_logging(level=settings.LOG_LEVEL)
    logger.info("Ragent 服务启动中 | version=%s | debug=%s", settings.APP_VERSION, settings.DEBUG)

    # 尝试初始化 Redis（失败不阻塞）
    redis_manager = None
    try:
        from ragent.common.redis_manager import get_redis_manager

        redis_manager = get_redis_manager()
        await redis_manager.init()
        logger.info("Redis 连接池初始化完成")
    except Exception as exc:
        logger.warning("Redis 连接池初始化失败（非致命）: %s", exc)
        redis_manager = None

    yield

    # ---- 关闭 ----
    if redis_manager is not None:
        try:
            await redis_manager.close()
            logger.info("Redis 连接池已关闭")
        except Exception as exc:
            logger.warning("Redis 连接池关闭异常: %s", exc)

    logger.info("Ragent 服务已关闭")


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。

    配置中间件（添加顺序 = 执行逆序，最后添加的最先执行）：
        1. ExceptionHandlerMiddleware（最先执行，捕获最外层异常）
        2. RequestContextMiddleware
        3. TraceMiddleware

    Returns:
        配置完毕的 ``FastAPI`` 应用实例。
    """
    settings = get_settings()

    app = FastAPI(
        title="Ragent API",
        description="RAG 智能问答平台 API",
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )

    # 中间件注册（最后添加的最先执行）
    app.add_middleware(ExceptionHandlerMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(TraceMiddleware)

    # 路由挂载
    app.include_router(router)

    return app


# ---------------------------------------------------------------------------
# 模块级应用实例（供 uvicorn 直接引用）
# ---------------------------------------------------------------------------

app: FastAPI = create_app()
