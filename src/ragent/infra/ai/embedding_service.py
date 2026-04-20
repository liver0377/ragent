"""向量嵌入服务模块 —— 封装 litellm.embedding 调用，支持路由降级和批量向量化。

核心职责：
    1. 提供 ``embed`` 单文本向量化接口
    2. 提供 ``embed_batch`` 批量文本向量化接口
    3. 通过 ``ModelSelector`` 实现多候选路由与自动降级
    4. 调用成功/失败后自动更新熔断器状态
"""

from __future__ import annotations

import logging
from typing import Any

import litellm

from ragent.common.exceptions import RemoteException
from ragent.config.settings import get_settings
from ragent.infra.ai.models import ModelCandidate, ModelConfigManager
from ragent.infra.ai.model_selector import ModelSelector

logger = logging.getLogger(__name__)


class EmbeddingService:
    """向量嵌入服务 —— 封装 litellm.embedding 调用，支持路由降级。

    使用方式::

        from ragent.infra.ai.models import ModelConfigManager
        from ragent.infra.ai.model_selector import ModelSelector
        from ragent.infra.ai.embedding_service import EmbeddingService

        mgr = ModelConfigManager()
        selector = ModelSelector(mgr)
        service = EmbeddingService(mgr, selector)

        # 单文本向量化（路由选择模型）
        vector = await service.embed("你好世界")

        # 指定模型向量化（跳过路由）
        vector = await service.embed("你好世界", model="embedding-3")

        # 批量向量化
        vectors = await service.embed_batch(["你好", "世界"])
    """

    def __init__(
        self,
        config_manager: ModelConfigManager,
        selector: ModelSelector,
    ) -> None:
        """初始化向量嵌入服务。

        Args:
            config_manager: 模型配置管理器，用于获取全局配置。
            selector: 模型选择器，用于获取候选列表和记录熔断器状态。
        """
        self._config = config_manager
        self._selector = selector

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    async def embed(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> list[float]:
        """单文本向量化。

        Args:
            text:  待向量化的文本内容。
            model: 指定嵌入模型名称。若为 ``None`` 则使用路由选择。

        Returns:
            list[float]: 文本的向量表示。

        Raises:
            RemoteException: 当所有候选模型均不可用时抛出（错误码 ``C3001``）。
        """
        results = await self.embed_batch([text], model=model)
        return results[0]

    async def embed_batch(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        """批量文本向量化。

        Args:
            texts: 待向量化的文本列表。
            model: 指定嵌入模型名称。若为 ``None`` 则使用路由选择。

        Returns:
            list[list[float]]: 每条文本对应的向量表示列表，顺序与输入一致。

        Raises:
            RemoteException: 当所有候选模型均不可用时抛出（错误码 ``C3001``）。
        """
        if model is not None:
            # 指定模型，直接调用
            return await self._call_embedding_direct(texts, model=model)

        # 未指定模型，使用路由降级
        return await self._call_embedding_with_fallback(texts)

    # ------------------------------------------------------------------ #
    # 直接调用（指定模型，无路由降级）
    # ------------------------------------------------------------------ #

    async def _call_embedding_direct(
        self,
        texts: list[str],
        *,
        model: str,
    ) -> list[list[float]]:
        """使用指定模型直接调用嵌入接口。

        Args:
            texts: 待向量化的文本列表。
            model: 指定的嵌入模型名称。

        Returns:
            list[list[float]]: 向量列表。
        """
        settings = get_settings()
        logger.debug("Embedding 直接调用: model=%s, 文本数量=%d", model, len(texts))

        # Embedding 使用硅基流动 API
        emb_api_key = settings.SILICONFLOW_API_KEY or settings.GLM_API_KEY
        emb_api_base = settings.SILICONFLOW_API_BASE or settings.GLM_BASE_URL

        response = await litellm.aembedding(
            model=model,
            input=texts,
            api_key=emb_api_key,
            api_base=emb_api_base,
        )

        # 按 index 排序确保顺序一致
        embeddings: list[list[float]] = [
            item["embedding"]  # type: ignore[index]
            for item in sorted(response.data, key=lambda d: d["index"])  # type: ignore[arg-type]
        ]
        return embeddings

    # ------------------------------------------------------------------ #
    # 路由降级调用
    # ------------------------------------------------------------------ #

    async def _call_embedding_with_fallback(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """使用路由降级策略进行嵌入调用。

        策略：
            1. 从 selector 获取 embedding 候选列表
            2. 按优先级依次尝试调用
            3. 成功则记录成功并返回
            4. 失败则记录失败，尝试下一个候选
            5. 全部失败抛出 RemoteException

        Args:
            texts: 待向量化的文本列表。

        Returns:
            list[list[float]]: 向量列表。

        Raises:
            RemoteException: 当所有候选模型均不可用时抛出。
        """
        candidates = self._selector.select_candidates("embedding")
        settings = get_settings()

        errors: list[str] = []
        for candidate in candidates:
            try:
                logger.debug(
                    "Embedding 路由调用: model=%s, timeout=%.1f, 文本数量=%d",
                    candidate.model_name,
                    candidate.timeout,
                    len(texts),
                )
                # Embedding 使用硅基流动 API
                emb_api_key = settings.SILICONFLOW_API_KEY or settings.GLM_API_KEY
                emb_api_base = settings.SILICONFLOW_API_BASE or settings.GLM_BASE_URL

                response = await litellm.aembedding(
                    model=candidate.model_name,
                    input=texts,
                    api_key=emb_api_key,
                    api_base=emb_api_base,
                    timeout=candidate.timeout,
                )

                # 按 index 排序确保顺序与输入一致
                embeddings: list[list[float]] = [
                    item["embedding"]  # type: ignore[index]
                    for item in sorted(response.data, key=lambda d: d["index"])  # type: ignore[arg-type]
                ]

                # 调用成功，记录到熔断器
                self._selector.record_success(candidate.model_name)
                return embeddings

            except Exception as exc:
                # 调用失败，记录到熔断器
                self._selector.record_failure(candidate.model_name)
                error_msg = f"嵌入模型 {candidate.model_name} 调用失败: {exc}"
                logger.warning(error_msg)
                errors.append(error_msg)

        # 所有候选均失败
        raise RemoteException(
            error_code="C3001",
            message=f"所有嵌入模型候选均不可用: {'; '.join(errors)}",
        )
