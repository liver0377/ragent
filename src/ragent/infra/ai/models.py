"""模型配置模块 —— 定义模型候选、路由配置及配置管理器。

本模块负责：
    1. 定义 ``ModelCandidate`` / ``ModelConfig`` 等 Pydantic 数据模型
    2. 通过 ``ModelConfigManager`` 从 YAML 文件加载配置，或回退到环境变量默认值
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ragent.config.settings import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    """任务类型枚举。"""

    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"


# ---------------------------------------------------------------------------
# Pydantic 数据模型
# ---------------------------------------------------------------------------

class ModelCandidate(BaseModel):
    """模型候选配置。

    Attributes:
        model_name: litellm 兼容的模型名称（如 ``"glm-4-flash"``、``"openai/gpt-4"``）
        provider:   供应商名称（可选）
        priority:   优先级，数值越小优先级越高（默认 0）
        timeout:    单次请求超时时间，单位秒（默认 30.0）
        max_retries: 最大重试次数（默认 2）
        enabled:    是否启用该候选（默认 True）
    """

    model_name: str
    provider: str = ""
    priority: int = 0
    timeout: float = 30.0
    max_retries: int = 2
    enabled: bool = True


class CircuitBreakerConfig(BaseModel):
    """熔断器配置。

    Attributes:
        failure_threshold: 连续失败多少次后开启熔断（默认 5）
        recovery_timeout:  熔断开启后等待多少秒进入半开状态（默认 60）
        success_threshold: 半开状态下连续成功多少次后关闭熔断（默认 3）
    """

    failure_threshold: int = 5
    recovery_timeout: int = 60
    success_threshold: int = 3


class StreamConfig(BaseModel):
    """流式响应配置。

    Attributes:
        first_packet_timeout: 等待首个数据包的超时时间，单位秒（默认 60.0）
    """

    first_packet_timeout: float = 60.0


class ModelConfig(BaseModel):
    """模型路由总配置。

    Attributes:
        chat_models:      对话模型候选列表
        embedding_models: 向量嵌入模型候选列表
        rerank_models:    重排序模型候选列表
        circuit_breaker:  熔断器参数
        stream:           流式响应参数
    """

    chat_models: list[ModelCandidate] = []
    embedding_models: list[ModelCandidate] = []
    rerank_models: list[ModelCandidate] = []
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()
    stream: StreamConfig = StreamConfig()


# ---------------------------------------------------------------------------
# 配置管理器
# ---------------------------------------------------------------------------

class ModelConfigManager:
    """模型配置管理器 —— 从 YAML 文件加载配置，支持环境变量默认值回退。

    使用方式::

        # 方式一：使用默认配置（来自环境变量）
        mgr = ModelConfigManager()

        # 方式二：从 YAML 文件加载
        mgr = ModelConfigManager.from_yaml("config/models.yaml")

        # 获取某类任务的候选列表
        candidates = mgr.get_candidates("chat")
    """

    def __init__(self, config: ModelConfig | None = None) -> None:
        """初始化配置管理器。

        Args:
            config: 模型配置实例。若为 ``None``，则使用环境变量默认值构建。
        """
        if config is not None:
            self._config = config
        else:
            self._config = self._build_default_config()

    # ---- 工厂方法 ----

    @classmethod
    def from_yaml(cls, path: str | Path) -> ModelConfigManager:
        """从 YAML 文件加载配置。

        Args:
            path: YAML 配置文件路径。

        Returns:
            ModelConfigManager: 使用文件内容初始化的配置管理器。

        Raises:
            FileNotFoundError: 当文件不存在时抛出。
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"模型配置文件不存在: {file_path}")

        raw: dict[str, Any] = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        config = ModelConfig(**raw)
        return cls(config=config)

    # ---- 公共接口 ----

    @property
    def config(self) -> ModelConfig:
        """获取当前模型配置。"""
        return self._config

    def get_candidates(self, task_type: str) -> list[ModelCandidate]:
        """获取指定任务类型的候选模型列表（不过滤、不排序，返回原始列表副本）。

        Args:
            task_type: 任务类型，如 ``"chat"``、``"embedding"``、``"rerank"``。

        Returns:
            list[ModelCandidate]: 候选模型列表的副本。

        Raises:
            ValueError: 当任务类型不被识别时抛出。
        """
        mapping: dict[str, list[ModelCandidate]] = {
            TaskType.CHAT.value: self._config.chat_models,
            TaskType.EMBEDDING.value: self._config.embedding_models,
            TaskType.RERANK.value: self._config.rerank_models,
        }
        if task_type not in mapping:
            raise ValueError(f"未知的任务类型: {task_type!r}，可选值: {list(mapping.keys())}")
        return list(mapping[task_type])

    # ---- 内部辅助 ----

    @staticmethod
    def _build_default_config() -> ModelConfig:
        """基于环境变量构建默认模型配置。"""
        settings = get_settings()
        return ModelConfig(
            chat_models=[
                ModelCandidate(
                    model_name=settings.GLM_MODEL,
                    provider="zhipu",
                    priority=0,
                ),
            ],
            embedding_models=[
                ModelCandidate(
                    model_name=settings.EMBEDDING_MODEL,
                    provider="zhipu",
                    priority=0,
                ),
            ],
            rerank_models=[],
        )
