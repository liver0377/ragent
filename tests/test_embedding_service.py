"""向量嵌入服务模块的单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragent.common.exceptions import RemoteException
from ragent.infra.ai.embedding_service import EmbeddingService
from ragent.infra.ai.models import (
    CircuitBreakerConfig,
    ModelCandidate,
    ModelConfig,
    ModelConfigManager,
)
from ragent.infra.ai.model_selector import ModelSelector


# --------------------------------------------------------------------------- #
# 辅助工厂
# --------------------------------------------------------------------------- #


def _make_manager(
    embedding: list[ModelCandidate] | None = None,
    cb_config: CircuitBreakerConfig | None = None,
) -> ModelConfigManager:
    """构建用于测试的 ModelConfigManager。"""
    config = ModelConfig(
        embedding_models=embedding or [],
        circuit_breaker=cb_config or CircuitBreakerConfig(),
    )
    return ModelConfigManager(config=config)


def _make_service(
    embedding: list[ModelCandidate] | None = None,
    cb_config: CircuitBreakerConfig | None = None,
) -> tuple[EmbeddingService, ModelSelector]:
    """构建用于测试的 EmbeddingService 和对应的 ModelSelector。"""
    mgr = _make_manager(embedding=embedding, cb_config=cb_config)
    selector = ModelSelector(mgr)
    service = EmbeddingService(mgr, selector)
    return service, selector


def _mock_embedding_response(
    embeddings: list[list[float]],
) -> MagicMock:
    """构造模拟的 litellm.aembedding 响应。

    Args:
        embeddings: 向量列表，索引对应输入文本的顺序。

    Returns:
        MagicMock: 模拟的 EmbeddingResponse 对象。
    """
    data: list[dict[str, object]] = []
    for i, emb in enumerate(embeddings):
        data.append({
            "embedding": emb,
            "index": i,
        })

    response = MagicMock()
    response.data = data
    return response


# --------------------------------------------------------------------------- #
# embed 测试 —— 单文本向量化
# --------------------------------------------------------------------------- #


class TestEmbed:
    """embed() 单文本向量化测试。"""

    @pytest.mark.asyncio
    async def test_embed_single_text_direct(self) -> None:
        """指定模型直接调用 embed。"""
        service, _ = _make_service(
            embedding=[ModelCandidate(model_name="embed-1", priority=0)],
        )

        mock_response = _mock_embedding_response([[0.1, 0.2, 0.3]])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(return_value=mock_response)

            result = await service.embed("你好世界", model="embed-1")

        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_embed_single_text_routing(self) -> None:
        """路由调用 embed。"""
        service, _ = _make_service(
            embedding=[ModelCandidate(model_name="embed-1", priority=0)],
        )

        mock_response = _mock_embedding_response([[0.5, 0.6]])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(return_value=mock_response)

            result = await service.embed("测试文本")

        assert result == [0.5, 0.6]


# --------------------------------------------------------------------------- #
# embed_batch 测试 —— 批量向量化（直接调用）
# --------------------------------------------------------------------------- #


class TestEmbedBatchDirect:
    """embed_batch() 指定模型的直接调用测试。"""

    @pytest.mark.asyncio
    async def test_embed_batch_direct(self) -> None:
        """指定模型直接批量调用。"""
        service, _ = _make_service(
            embedding=[ModelCandidate(model_name="embed-1", priority=0)],
        )

        mock_response = _mock_embedding_response([
            [0.1, 0.2],
            [0.3, 0.4],
        ])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(return_value=mock_response)

            result = await service.embed_batch(
                ["文本1", "文本2"],
                model="embed-1",
            )

        assert result == [[0.1, 0.2], [0.3, 0.4]]

    @pytest.mark.asyncio
    async def test_embed_batch_passes_correct_model(self) -> None:
        """直接调用应传递正确的模型名称。"""
        service, _ = _make_service()

        mock_response = _mock_embedding_response([[0.1]])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(return_value=mock_response)

            await service.embed_batch(["文本"], model="my-embed-model")

        call_kwargs = mock_litellm.aembedding.call_args
        assert call_kwargs.kwargs["model"] == "my-embed-model"

    @pytest.mark.asyncio
    async def test_embed_batch_single_text(self) -> None:
        """批量调用只传入一个文本时也应正常工作。"""
        service, _ = _make_service()

        mock_response = _mock_embedding_response([[0.5, 0.5]])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(return_value=mock_response)

            result = await service.embed_batch(["一条文本"], model="embed-1")

        assert result == [[0.5, 0.5]]


# --------------------------------------------------------------------------- #
# embed_batch 测试 —— 路由降级
# --------------------------------------------------------------------------- #


class TestEmbedBatchWithFallback:
    """embed_batch() 路由降级调用测试。"""

    @pytest.mark.asyncio
    async def test_routing_success_on_first(self) -> None:
        """路由调用 —— 第一个候选成功。"""
        service, selector = _make_service(
            embedding=[
                ModelCandidate(model_name="embed-a", priority=0),
                ModelCandidate(model_name="embed-b", priority=1),
            ],
        )

        mock_response = _mock_embedding_response([[0.1], [0.2]])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(return_value=mock_response)

            with patch.object(selector, "record_success") as mock_success:
                result = await service.embed_batch(["文本1", "文本2"])

        assert result == [[0.1], [0.2]]
        assert mock_litellm.aembedding.await_count == 1
        mock_success.assert_called_once_with("embed-a")

    @pytest.mark.asyncio
    async def test_routing_fallback_on_failure(self) -> None:
        """路由调用 —— 第一个候选失败，降级到第二个。"""
        service, selector = _make_service(
            embedding=[
                ModelCandidate(model_name="embed-a", priority=0),
                ModelCandidate(model_name="embed-b", priority=1),
            ],
        )

        mock_response_b = _mock_embedding_response([[0.9], [0.8]])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(
                side_effect=[
                    Exception("embed-a 不可用"),
                    mock_response_b,
                ],
            )

            with patch.object(selector, "record_failure") as mock_failure, \
                 patch.object(selector, "record_success") as mock_success:
                result = await service.embed_batch(["文本1", "文本2"])

        assert result == [[0.9], [0.8]]
        mock_failure.assert_called_once_with("embed-a")
        mock_success.assert_called_once_with("embed-b")

    @pytest.mark.asyncio
    async def test_routing_all_fail_raises(self) -> None:
        """路由调用 —— 所有候选均失败，抛出 RemoteException。"""
        service, selector = _make_service(
            embedding=[
                ModelCandidate(model_name="embed-a", priority=0),
                ModelCandidate(model_name="embed-b", priority=1),
            ],
        )

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(
                side_effect=Exception("服务不可用"),
            )

            with pytest.raises(RemoteException) as exc_info:
                await service.embed_batch(["文本1"])

        assert exc_info.value.error_code == "C3001"
        assert "所有嵌入模型候选均不可用" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_routing_uses_candidate_timeout(self) -> None:
        """路由调用应传递候选模型的 timeout 参数。"""
        service, _ = _make_service(
            embedding=[
                ModelCandidate(
                    model_name="embed-a",
                    priority=0,
                    timeout=45.0,
                ),
            ],
        )

        mock_response = _mock_embedding_response([[0.1]])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(return_value=mock_response)

            await service.embed_batch(["文本1"])

        call_kwargs = mock_litellm.aembedding.call_args
        assert call_kwargs.kwargs["timeout"] == 45.0

    @pytest.mark.asyncio
    async def test_routing_records_failure_each_attempt(self) -> None:
        """路由降级应记录每个失败候选。"""
        service, selector = _make_service(
            embedding=[
                ModelCandidate(model_name="embed-a", priority=0),
                ModelCandidate(model_name="embed-b", priority=1),
                ModelCandidate(model_name="embed-c", priority=2),
            ],
        )

        mock_response_c = _mock_embedding_response([[0.1]])

        with patch("ragent.infra.ai.embedding_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.embedding_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.aembedding = AsyncMock(
                side_effect=[
                    Exception("embed-a 失败"),
                    Exception("embed-b 失败"),
                    mock_response_c,
                ],
            )

            with patch.object(selector, "record_failure") as mock_failure, \
                 patch.object(selector, "record_success") as mock_success:
                result = await service.embed_batch(["文本1"])

        assert result == [[0.1]]
        assert mock_failure.call_count == 2
        mock_failure.assert_any_call("embed-a")
        mock_failure.assert_any_call("embed-b")
        mock_success.assert_called_once_with("embed-c")


# --------------------------------------------------------------------------- #
# 空候选列表测试
# --------------------------------------------------------------------------- #


class TestEmptyCandidates:
    """候选模型为空时的行为测试。"""

    @pytest.mark.asyncio
    async def test_embed_no_candidates_raises(self) -> None:
        """无候选模型时 embed 应抛出异常。"""
        service, _ = _make_service(embedding=[])

        with pytest.raises(RemoteException, match="C3001"):
            await service.embed("测试文本")

    @pytest.mark.asyncio
    async def test_embed_batch_no_candidates_raises(self) -> None:
        """无候选模型时 embed_batch 应抛出异常。"""
        service, _ = _make_service(embedding=[])

        with pytest.raises(RemoteException, match="C3001"):
            await service.embed_batch(["测试文本"])
