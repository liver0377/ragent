"""LLM 对话服务模块的单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragent.common.exceptions import RemoteException
from ragent.infra.ai.llm_service import LLMService
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
    chat: list[ModelCandidate] | None = None,
    cb_config: CircuitBreakerConfig | None = None,
) -> ModelConfigManager:
    """构建用于测试的 ModelConfigManager。"""
    config = ModelConfig(
        chat_models=chat or [],
        circuit_breaker=cb_config or CircuitBreakerConfig(),
    )
    return ModelConfigManager(config=config)


def _make_service(
    chat: list[ModelCandidate] | None = None,
    cb_config: CircuitBreakerConfig | None = None,
) -> tuple[LLMService, ModelSelector]:
    """构建用于测试的 LLMService 和对应的 ModelSelector。"""
    mgr = _make_manager(chat=chat, cb_config=cb_config)
    selector = ModelSelector(mgr)
    service = LLMService(mgr, selector)
    return service, selector


def _mock_completion_response(content: str = "你好，我是AI助手") -> MagicMock:
    """构造模拟的非流式 litellm.acompletion 响应。"""
    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


def _mock_stream_chunks(tokens: list[str]) -> list[MagicMock]:
    """构造模拟的流式 litellm.acompletion 响应数据块列表。"""
    chunks: list[MagicMock] = []
    for token in tokens:
        delta = MagicMock()
        delta.content = token

        choice = MagicMock()
        choice.delta = delta

        chunk = MagicMock()
        chunk.choices = [choice]
        chunks.append(chunk)

    # 最后一个 chunk 的 delta.content 为 None（结束标记）
    delta_end = MagicMock()
    delta_end.content = None

    choice_end = MagicMock()
    choice_end.delta = delta_end

    chunk_end = MagicMock()
    chunk_end.choices = [choice_end]
    chunks.append(chunk_end)

    return chunks


# --------------------------------------------------------------------------- #
# chat 测试 —— 指定模型（直接调用，无路由）
# --------------------------------------------------------------------------- #


class TestChatDirect:
    """chat() 指定模型的直接调用测试。"""

    @pytest.mark.asyncio
    async def test_chat_direct_returns_content(self) -> None:
        """指定模型时直接调用，返回响应文本。"""
        service, _ = _make_service()

        mock_response = _mock_completion_response("这是测试回复")

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            result = await service.chat(
                [{"role": "user", "content": "你好"}],
                model="glm-4-flash",
            )

        assert result == "这是测试回复"
        mock_litellm.acompletion.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chat_direct_passes_params(self) -> None:
        """指定模型时应正确传递所有参数。"""
        service, _ = _make_service()

        mock_response = _mock_completion_response("ok")

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            await service.chat(
                [{"role": "user", "content": "你好"}],
                model="glm-4-flash",
                temperature=0.5,
                max_tokens=100,
            )

        call_kwargs = mock_litellm.acompletion.call_args
        assert call_kwargs.kwargs["model"] == "glm-4-flash"
        assert call_kwargs.kwargs["temperature"] == 0.5
        assert call_kwargs.kwargs["max_tokens"] == 100
        assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "你好"}]

    @pytest.mark.asyncio
    async def test_chat_direct_no_max_tokens_when_none(self) -> None:
        """max_tokens 为 None 时不应传递该参数。"""
        service, _ = _make_service()

        mock_response = _mock_completion_response("ok")

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            await service.chat(
                [{"role": "user", "content": "你好"}],
                model="glm-4-flash",
            )

        call_kwargs = mock_litellm.acompletion.call_args
        assert "max_tokens" not in call_kwargs.kwargs


# --------------------------------------------------------------------------- #
# chat 测试 —— 路由降级
# --------------------------------------------------------------------------- #


class TestChatWithFallback:
    """chat() 路由降级调用测试。"""

    @pytest.mark.asyncio
    async def test_chat_routing_success_on_first(self) -> None:
        """路由调用 —— 第一个候选成功，直接返回。"""
        service, selector = _make_service(
            chat=[
                ModelCandidate(model_name="model-a", priority=0),
                ModelCandidate(model_name="model-b", priority=1),
            ],
        )

        mock_response = _mock_completion_response("来自model-a的回复")

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            result = await service.chat([{"role": "user", "content": "你好"}])

        assert result == "来自model-a的回复"
        # 只调用了一次（第一个候选成功）
        assert mock_litellm.acompletion.await_count == 1

    @pytest.mark.asyncio
    async def test_chat_routing_fallback_on_failure(self) -> None:
        """路由调用 —— 第一个候选失败，降级到第二个候选。"""
        service, selector = _make_service(
            chat=[
                ModelCandidate(model_name="model-a", priority=0),
                ModelCandidate(model_name="model-b", priority=1),
            ],
        )

        mock_response_b = _mock_completion_response("来自model-b的回复")

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=[
                    Exception("model-a 不可用"),
                    mock_response_b,
                ],
            )

            result = await service.chat([{"role": "user", "content": "你好"}])

        assert result == "来自model-b的回复"
        assert mock_litellm.acompletion.await_count == 2
        # model-a 应记录失败
        # model-b 应记录成功

    @pytest.mark.asyncio
    async def test_chat_routing_all_fail_raises(self) -> None:
        """路由调用 —— 所有候选均失败，抛出 RemoteException。"""
        service, selector = _make_service(
            chat=[
                ModelCandidate(model_name="model-a", priority=0),
                ModelCandidate(model_name="model-b", priority=1),
            ],
        )

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=Exception("服务不可用"),
            )

            with pytest.raises(RemoteException) as exc_info:
                await service.chat([{"role": "user", "content": "你好"}])

        assert exc_info.value.error_code == "C3001"
        assert "所有模型候选均不可用" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_chat_routing_records_success(self) -> None:
        """路由调用成功后应记录成功到 selector。"""
        service, selector = _make_service(
            chat=[ModelCandidate(model_name="model-a", priority=0)],
        )

        mock_response = _mock_completion_response("ok")

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            with patch.object(selector, "record_success") as mock_success, \
                 patch.object(selector, "record_failure") as mock_failure:
                await service.chat([{"role": "user", "content": "你好"}])

        mock_success.assert_called_once_with("model-a")
        mock_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_routing_records_failure_on_error(self) -> None:
        """路由调用失败后应记录失败到 selector。"""
        service, selector = _make_service(
            chat=[
                ModelCandidate(model_name="model-a", priority=0),
                ModelCandidate(model_name="model-b", priority=1),
            ],
        )

        mock_response_b = _mock_completion_response("ok")

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=[
                    Exception("model-a 失败"),
                    mock_response_b,
                ],
            )

            with patch.object(selector, "record_success") as mock_success, \
                 patch.object(selector, "record_failure") as mock_failure:
                await service.chat([{"role": "user", "content": "你好"}])

        # model-a 失败，model-b 成功
        mock_failure.assert_called_once_with("model-a")
        mock_success.assert_called_once_with("model-b")

    @pytest.mark.asyncio
    async def test_chat_routing_uses_candidate_timeout(self) -> None:
        """路由调用应传递候选模型的 timeout 参数。"""
        service, _ = _make_service(
            chat=[
                ModelCandidate(
                    model_name="model-a",
                    priority=0,
                    timeout=60.0,
                ),
            ],
        )

        mock_response = _mock_completion_response("ok")

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            await service.chat([{"role": "user", "content": "你好"}])

        call_kwargs = mock_litellm.acompletion.call_args
        assert call_kwargs.kwargs["timeout"] == 60.0


# --------------------------------------------------------------------------- #
# stream_chat 测试 —— 指定模型（直接调用）
# --------------------------------------------------------------------------- #


class TestStreamChatDirect:
    """stream_chat() 指定模型的直接流式调用测试。"""

    @pytest.mark.asyncio
    async def test_stream_direct_yields_tokens(self) -> None:
        """指定模型流式调用应逐 token 返回内容。"""
        service, _ = _make_service()

        chunks = _mock_stream_chunks(["你", "好", "世界"])

        # 创建一个异步迭代器来模拟流式响应
        async def _async_iter():
            for chunk in chunks:
                yield chunk

        mock_stream_response = _async_iter()

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            # acompletion 返回异步迭代器
            mock_litellm.acompletion = AsyncMock(return_value=mock_stream_response)

            tokens: list[str] = []
            async for token in service.stream_chat(
                [{"role": "user", "content": "你好"}],
                model="glm-4-flash",
            ):
                tokens.append(token)

        assert tokens == ["你", "好", "世界"]

    @pytest.mark.asyncio
    async def test_stream_direct_skips_none_content(self) -> None:
        """流式调用应跳过 delta.content 为 None 的数据块。"""
        service, _ = _make_service()

        chunks = _mock_stream_chunks(["你", "好"])
        # _mock_stream_chunks 已包含一个 content=None 的结束块

        async def _async_iter():
            for chunk in chunks:
                yield chunk

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=_async_iter())

            tokens: list[str] = []
            async for token in service.stream_chat(
                [{"role": "user", "content": "你好"}],
                model="glm-4-flash",
            ):
                tokens.append(token)

        # 只有非 None 的 token 被产出
        assert tokens == ["你", "好"]

    @pytest.mark.asyncio
    async def test_stream_direct_passes_stream_true(self) -> None:
        """流式调用应传递 stream=True 参数。"""
        service, _ = _make_service()

        async def _async_iter():
            yield _mock_stream_chunks(["ok"])[0]

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=_async_iter())

            # 消费生成器
            _ = [
                token
                async for token in service.stream_chat(
                    [{"role": "user", "content": "你好"}],
                    model="glm-4-flash",
                )
            ]

        call_kwargs = mock_litellm.acompletion.call_args
        assert call_kwargs.kwargs["stream"] is True


# --------------------------------------------------------------------------- #
# stream_chat 测试 —— 路由降级
# --------------------------------------------------------------------------- #


class TestStreamChatWithFallback:
    """stream_chat() 路由降级流式调用测试。"""

    @pytest.mark.asyncio
    async def test_stream_routing_success(self) -> None:
        """路由流式调用成功。"""
        service, selector = _make_service(
            chat=[
                ModelCandidate(model_name="model-a", priority=0),
            ],
        )

        chunks = _mock_stream_chunks(["你好"])

        async def _async_iter():
            for chunk in chunks:
                yield chunk

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(return_value=_async_iter())

            with patch.object(selector, "record_success") as mock_success:
                tokens: list[str] = []
                async for token in service.stream_chat(
                    [{"role": "user", "content": "你好"}],
                ):
                    tokens.append(token)

        assert tokens == ["你好"]
        mock_success.assert_called_once_with("model-a")

    @pytest.mark.asyncio
    async def test_stream_routing_fallback_on_failure(self) -> None:
        """路由流式调用 —— 第一个候选失败，降级到第二个。"""
        service, selector = _make_service(
            chat=[
                ModelCandidate(model_name="model-a", priority=0),
                ModelCandidate(model_name="model-b", priority=1),
            ],
        )

        chunks_b = _mock_stream_chunks(["来自", "model-b"])

        async def _async_iter_b():
            for chunk in chunks_b:
                yield chunk

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )

            # 第一次调用失败，第二次成功
            call_count = 0

            async def _mock_acompletion(**kwargs: object):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("model-a 流式失败")
                return _async_iter_b()

            mock_litellm.acompletion = _mock_acompletion

            with patch.object(selector, "record_failure") as mock_failure, \
                 patch.object(selector, "record_success") as mock_success:
                tokens: list[str] = []
                async for token in service.stream_chat(
                    [{"role": "user", "content": "你好"}],
                ):
                    tokens.append(token)

        assert tokens == ["来自", "model-b"]
        mock_failure.assert_called_once_with("model-a")
        mock_success.assert_called_once_with("model-b")

    @pytest.mark.asyncio
    async def test_stream_routing_all_fail_raises(self) -> None:
        """路由流式调用 —— 所有候选均失败，抛出 RemoteException。"""
        service, selector = _make_service(
            chat=[
                ModelCandidate(model_name="model-a", priority=0),
                ModelCandidate(model_name="model-b", priority=1),
            ],
        )

        with patch("ragent.infra.ai.llm_service.litellm") as mock_litellm, \
             patch("ragent.infra.ai.llm_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                GLM_API_KEY="test-key",
                GLM_BASE_URL="https://api.test.com",
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=Exception("服务不可用"),
            )

            with pytest.raises(RemoteException) as exc_info:
                async for _ in service.stream_chat(
                    [{"role": "user", "content": "你好"}],
                ):
                    pass

        assert exc_info.value.error_code == "C3001"


# --------------------------------------------------------------------------- #
# 空候选列表测试
# --------------------------------------------------------------------------- #


class TestEmptyCandidates:
    """候选模型为空时的行为测试。"""

    @pytest.mark.asyncio
    async def test_chat_no_candidates_raises(self) -> None:
        """无候选模型时 chat 应抛出异常。"""
        service, _ = _make_service(chat=[])

        with pytest.raises(RemoteException, match="C3001"):
            await service.chat([{"role": "user", "content": "你好"}])

    @pytest.mark.asyncio
    async def test_stream_no_candidates_raises(self) -> None:
        """无候选模型时 stream_chat 应抛出异常。"""
        service, _ = _make_service(chat=[])

        with pytest.raises(RemoteException, match="C3001"):
            async for _ in service.stream_chat(
                [{"role": "user", "content": "你好"}],
            ):
                pass
