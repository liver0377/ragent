"""配置管理模块。

使用 pydantic-settings 从环境变量和 .env 文件加载应用配置。
所有配置项均有合理的默认值，支持通过环境变量覆盖。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 文件所在目录（项目根目录）
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """应用全局配置。

    配置优先级（从高到低）：
        1. 系统环境变量
        2. .env 文件
        3. 字段默认值
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ==================== AI 模型配置 ====================

    GLM_API_KEY: str = Field(
        default="",
        description="智谱AI API Key",
    )
    GLM_BASE_URL: str = Field(
        default="https://open.bigmodel.cn/api/coding/paas/v4",
        description="API base URL",
    )
    GLM_MODEL: str = Field(
        default="openai/glm-4-flash",
        description="默认聊天模型（litellm 格式，需带 provider 前缀）",
    )
    EMBEDDING_MODEL: str = Field(
        default="openai/embedding-3",
        description="Embedding 模型（litellm 格式，需带 provider 前缀）",
    )

    # ==================== 基础设施配置 ====================

    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis 连接URL",
    )
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://ragent:ragent@localhost:5432/ragent",
        description="PostgreSQL 异步连接URL（asyncpg 驱动）",
    )

    MILVUS_HOST: str = Field(
        default="localhost",
        description="Milvus 主机",
    )
    MILVUS_PORT: int = Field(
        default=19530,
        description="Milvus 端口",
    )
    CELERY_BROKER_URL: str = Field(
        default="redis://localhost:6379/1",
        description="Celery Broker",
    )
    CELERY_RESULT_BACKEND: str = Field(
        default="redis://localhost:6379/2",
        description="Celery Result Backend",
    )

    # ==================== 应用配置 ====================

    APP_NAME: str = Field(
        default="ragent",
        description="应用名称",
    )
    APP_VERSION: str = Field(
        default="0.1.0",
        description="应用版本",
    )
    DEBUG: bool = Field(
        default=False,
        description="调试模式开关",
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="日志级别",
    )
    API_PREFIX: str = Field(
        default="/api/v1",
        description="API 路由前缀",
    )

    # ==================== JWT 认证配置 ====================

    JWT_SECRET_KEY: str = Field(
        default="ragent-jwt-secret-change-in-production-2026",
        description="JWT 签名密钥（生产环境务必更换）",
    )
    JWT_ALGORITHM: str = Field(
        default="HS256",
        description="JWT 签名算法",
    )
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=1440,
        description="Access Token 过期时间（分钟），默认 24 小时",
    )

    # ==================== LLM 调用配置 ====================

    LLM_TIMEOUT: int = Field(
        default=60,
        description="LLM 请求超时秒数",
    )
    LLM_MAX_RETRIES: int = Field(
        default=3,
        description="最大重试次数",
    )
    EMBEDDING_DIMENSION: int = Field(
        default=2048,
        description="向量维度",
    )

    # ==================== 文本处理配置 ====================

    CHUNK_SIZE: int = Field(
        default=512,
        description="文本分块大小",
    )
    CHUNK_OVERLAP: int = Field(
        default=64,
        description="分块重叠字符数",
    )
    RETRIEVAL_TOP_K: int = Field(
        default=5,
        description="检索返回数量",
    )
    RATE_LIMIT_MAX_CONCURRENT: int = Field(
        default=10,
        description="最大并发请求数",
    )
    RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        description="限流窗口秒数",
    )
    SESSION_MAX_ROUNDS: int = Field(
        default=10,
        description="会话最大轮次",
    )
    SESSION_SUMMARY_THRESHOLD: int = Field(
        default=6,
        description="会话摘要触发轮次",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。

    使用 lru_cache 确保配置只加载一次，后续调用直接返回缓存实例。

    Returns:
        Settings: 全局配置实例
    """
    return Settings()
