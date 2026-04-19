"""LLM 对话服务模块 —— 封装 litellm 统一调用，支持路由降级和流式响应。

核心职责：
    1. 提供 ``chat`` 非流式对话接口
    2. 提供 ``stream_chat`` 流式对话接口（异步生成器）
    3. 通过 ``ModelSelector`` 实现多候选路由与自动降级
    4. 调用成功/失败后自动更新熔断器状态
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import litellm

from ragent.common.exceptions import RemoteException
from ragent.config.settings import get_settings
from ragent.infra.ai.models import ModelCandidate, ModelConfigManager
from ragent.infra.ai.model_selector import ModelSelector

logger = logging.getLogger(__name__)


class LLMService:
    """LLM 对话服务 —— 封装 litellm.completion 调用，支持路由降级。

    使用方式::

        from ragent.infra.ai.models import ModelConfigManager
        from ragent.infra.ai.model_selector import ModelSelector
        from ragent.infra.ai.llm_service import LLMService

        mgr = ModelConfigManager()
        selector = ModelSelector(mgr)
        service = LLMService(mgr, selector)

        # 普通对话（路由选择模型）
        reply = await service.chat([{"role": "user", "content": "你好"}])

        # 指定模型对话（跳过路由）
        reply = await service.chat(
            [{"role": "user", "content": "你好"}],
            model="glm-4-flash",
        )

        # 流式对话
        async for token in service.stream_chat(
            [{"role": "user", "content": "你好"}],
        ):
            print(token, end="", flush=True)
    """

    def __init__(
        self,
        config_manager: ModelConfigManager,
        selector: ModelSelector,
    ) -> None:
        """初始化 LLM 对话服务。

        Args:
            config_manager: 模型配置管理器，用于获取全局配置。
            selector: 模型选择器，用于获取候选列表和记录熔断器状态。
        """
        self._config = config_manager
        self._selector = selector

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        """普通对话 —— 单次请求，返回完整响应文本。

        Args:
            messages:    消息列表，格式为 ``[{"role": "user", "content": "..."}]``
            model:       指定模型名称。若为 ``None`` 则使用路由选择。
            temperature: 采样温度，默认 0.7。
            max_tokens:  最大生成 token 数，``None`` 表示使用模型默认值。
            **kwargs:    传递给 litellm.acompletion 的额外参数。

        Returns:
            str: 模型响应文本。

        Raises:
            RemoteException: 当所有候选模型均不可用时抛出（错误码 ``C3001``）。
        """
        if model is not None:
            # 指定模型，直接调用（跳过路由）
            return await self._call_direct(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

        # 未指定模型，使用路由降级
        return await self._call_with_fallback(
            task_type="chat",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式对话 —— 异步生成器，逐 token 返回。

        Args:
            messages:    消息列表。
            model:       指定模型名称。若为 ``None`` 则使用路由选择。
            temperature: 采样温度。
            max_tokens:  最大生成 token 数。
            **kwargs:    传递给 litellm.acompletion 的额外参数。

        Yields:
            str: 模型响应的文本片段（逐 token）。

        Raises:
            RemoteException: 当所有候选模型均不可用时抛出（错误码 ``C3001``）。
        """
        if model is not None:
            # 指定模型，直接流式调用
            async for token in self._stream_direct(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ):
                yield token
            return

        # 未指定模型，使用路由降级流式调用
        async for token in self._stream_with_fallback(
            task_type="chat",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield token

    # ------------------------------------------------------------------ #
    # 直接调用（指定模型，无路由降级）
    # ------------------------------------------------------------------ #

    async def _call_direct(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> str:
        """使用指定模型直接调用，不经过路由降级。

        Args:
            messages:    消息列表。
            model:       指定的模型名称。
            temperature: 采样温度。
            max_tokens:  最大生成 token 数。
            **kwargs:    额外参数。

        Returns:
            str: 模型响应文本。
        """
        settings = get_settings()
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "api_key": settings.GLM_API_KEY,
            "api_base": settings.GLM_BASE_URL,
            **kwargs,
        }
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens

        logger.debug("LLM 直接调用: model=%s", model)
        response = await litellm.acompletion(**call_kwargs)
        content: str = response.choices[0].message.content  # type: ignore[union-attr]
        return content

    async def _stream_direct(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """使用指定模型直接流式调用。

        Args:
            messages:    消息列表。
            model:       指定的模型名称。
            temperature: 采样温度。
            max_tokens:  最大生成 token 数。
            **kwargs:    额外参数。

        Yields:
            str: 模型响应的文本片段。
        """
        settings = get_settings()
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "api_key": settings.GLM_API_KEY,
            "api_base": settings.GLM_BASE_URL,
            **kwargs,
        }
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens

        logger.debug("LLM 直接流式调用: model=%s", model)
        response = await litellm.acompletion(**call_kwargs)
        async for chunk in response:
            delta_content = chunk.choices[0].delta.content  # type: ignore[union-attr]
            if delta_content is not None:
                yield delta_content

    # ------------------------------------------------------------------ #
    # 路由降级调用
    # ------------------------------------------------------------------ #

    async def _call_with_fallback(
        self,
        task_type: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> str:
        """使用路由降级策略进行非流式调用。

        策略：
            1. 从 selector 获取候选列表
            2. 按优先级依次尝试调用
            3. 成功则记录成功并返回
            4. 失败则记录失败，尝试下一个候选
            5. 全部失败抛出 RemoteException

        Args:
            task_type:   任务类型（如 ``"chat"``）。
            messages:    消息列表。
            temperature: 采样温度。
            max_tokens:  最大生成 token 数。
            **kwargs:    额外参数。

        Returns:
            str: 模型响应文本。

        Raises:
            RemoteException: 当所有候选模型均不可用时抛出。
        """
        candidates = self._selector.select_candidates(task_type)
        settings = get_settings()

        errors: list[str] = []
        for candidate in candidates:
            try:
                call_kwargs: dict[str, Any] = {
                    "model": candidate.model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "api_key": settings.GLM_API_KEY,
                    "api_base": settings.GLM_BASE_URL,
                    "timeout": candidate.timeout,
                    **kwargs,
                }
                if max_tokens is not None:
                    call_kwargs["max_tokens"] = max_tokens

                logger.debug(
                    "LLM 路由调用: model=%s, timeout=%.1f",
                    candidate.model_name,
                    candidate.timeout,
                )
                response = await litellm.acompletion(**call_kwargs)
                content: str = response.choices[0].message.content  # type: ignore[union-attr]

                # 调用成功，记录到熔断器
                self._selector.record_success(candidate.model_name)
                return content

            except Exception as exc:
                # 调用失败，记录到熔断器
                self._selector.record_failure(candidate.model_name)
                error_msg = f"模型 {candidate.model_name} 调用失败: {exc}"
                logger.warning(error_msg)
                errors.append(error_msg)

        # 所有候选均失败
        raise RemoteException(
            error_code="C3001",
            message=f"所有模型候选均不可用: {'; '.join(errors)}",
        )

    async def _stream_with_fallback(
        self,
        task_type: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """使用路由降级策略进行流式调用。

        注意：流式调用仅尝试第一个可用候选，失败则抛出异常。
        因为流式场景中降级到另一个模型的用户体验较差。

        Args:
            task_type:   任务类型。
            messages:    消息列表。
            temperature: 采样温度。
            max_tokens:  最大生成 token 数。
            **kwargs:    额外参数。

        Yields:
            str: 模型响应的文本片段。

        Raises:
            RemoteException: 当候选模型调用失败时抛出。
        """
        candidates = self._selector.select_candidates(task_type)
        settings = get_settings()

        for candidate in candidates:
            try:
                call_kwargs: dict[str, Any] = {
                    "model": candidate.model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                    "api_key": settings.GLM_API_KEY,
                    "api_base": settings.GLM_BASE_URL,
                    "timeout": candidate.timeout,
                    **kwargs,
                }
                if max_tokens is not None:
                    call_kwargs["max_tokens"] = max_tokens

                logger.debug(
                    "LLM 路由流式调用: model=%s, timeout=%.1f",
                    candidate.model_name,
                    candidate.timeout,
                )
                response = await litellm.acompletion(**call_kwargs)

                # 流式生成 —— 使用内部标志跟踪是否成功产出任何 token
                has_produced = False
                async for chunk in response:
                    delta_content = chunk.choices[0].delta.content  # type: ignore[union-attr]
                    if delta_content is not None:
                        has_produced = True
                        yield delta_content

                # 如果成功产出 token，记录成功并结束
                if has_produced:
                    self._selector.record_success(candidate.model_name)
                return

            except Exception as exc:
                # 调用失败，记录到熔断器并尝试下一个候选
                self._selector.record_failure(candidate.model_name)
                logger.warning(
                    "LLM 流式路由: 模型 %s 调用失败: %s，尝试下一个候选",
                    candidate.model_name,
                    exc,
                )
                continue

        # 所有候选均失败
        raise RemoteException(
            error_code="C3001",
            message="所有模型候选均不可用（流式调用）",
        )
