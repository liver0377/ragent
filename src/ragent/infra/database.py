"""
异步 SQLAlchemy 数据库引擎与会话管理。

提供：
    - ``async_engine``  — 全局异步引擎（通过 get_settings() 懒加载）
    - ``async_session_factory`` — AsyncSession 工厂
    - ``get_db()`` — FastAPI 依赖项，用于注入异步数据库会话
    - ``init_db()`` / ``close_db()`` — 生命周期管理

所有表使用 SQLAlchemy 2.0 声明式映射（见 common.models）。
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text as sa_text

from ragent.common.logging import get_logger
from ragent.common.models import Base

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 全局变量（懒初始化）
# ---------------------------------------------------------------------------

_async_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    """根据配置创建异步引擎。"""
    from ragent.config.settings import get_settings

    settings = get_settings()
    db_url: str = getattr(settings, "DATABASE_URL", "postgresql+asyncpg://ragent:ragent@localhost:5432/ragent")

    logger.info("创建数据库引擎: %s", _mask_password(db_url))

    return create_async_engine(
        db_url,
        echo=settings.DEBUG,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def _mask_password(url: str) -> str:
    """隐藏数据库 URL 中的密码。"""
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """初始化数据库引擎和会话工厂。

    在应用启动时调用（FastAPI lifespan）。
    首次调用时会自动创建所有表（开发阶段使用，生产环境用 Alembic 迁移）。
    """
    global _async_engine, _async_session_factory

    _async_engine = _build_engine()
    _async_session_factory = async_sessionmaker(
        bind=_async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # 开发阶段：自动建表（checkfirst 避免并发重复建表）
    async with _async_engine.begin() as conn:
        # 启用 pgvector 扩展
        await conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)

    logger.info("数据库初始化完成，表已同步")


async def close_db() -> None:
    """关闭数据库引擎，释放连接池。"""
    global _async_engine, _async_session_factory

    if _async_engine is not None:
        await _async_engine.dispose()
        logger.info("数据库引擎已关闭")

    _async_engine = None
    _async_session_factory = None


def get_engine() -> AsyncEngine:
    """获取当前异步引擎实例。"""
    if _async_engine is None:
        raise RuntimeError("数据库引擎未初始化，请先调用 init_db()")
    return _async_engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取会话工厂。"""
    if _async_session_factory is None:
        raise RuntimeError("数据库会话工厂未初始化，请先调用 init_db()")
    return _async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖项：注入异步数据库会话。

    用法::

        @router.get("/users/me")
        async def get_me(db: AsyncSession = Depends(get_db)):
            ...

    Yields:
        AsyncSession: 数据库会话，请求结束后自动关闭。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
