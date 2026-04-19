"""路由执行器模块 —— 管理模型降级迭代，支持普通调用与流式调用。

核心职责：
    1. ``RoutingExecutor.execute``   —— 对普通异步调用执行多候选降级
    2. ``RoutingExecutor.execute_stream`` —— 对流式异步迭代器执行多候选降级（首包探测）
    3. 调用成功/失败后自动通过 ``ModelSelector`` 更新熔断器状态
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Generic, TypeVar

from ragent.common.exceptions import RemoteException
from ragent.infra.ai.model_selector import ModelSelector
from ragent.infra.ai.probe_stream import ProbeStreamBridge

logger = logging.getLogger(__name__)

# 泛型类型变量，表示调用函数的返回值类型
T = TypeVar("T")


class RoutingExecutor(Generic[T]):
    """路由执行器 —— 管理模型降级迭代。

    本执行器是一个通用的降级编排器，**不包含**具体的模型调用逻辑。
    调用方只需提供：

    - ``task_type`` —— 任务类型（如 ``"chat"``、``"embedding"``、``"rerank"``）
    - ``call_fn``   —— 接受 ``ModelCandidate`` 并返回 ``Awaitable[T]`` 的调用函数

    执行器负责：
        1. 从 ``ModelSelector`` 获取候选列表
        2. 按优先级依次对每个候选调用 ``call_fn``
        3. 成功则通过 ``selector.record_success`` 记录并返回结果
        4. 失败则通过 ``selector.record_failure`` 记录，尝试下一个候选
        5. 全部失败时抛出 ``RemoteException``

    使用示例::

        from ragent.infra.ai.models import ModelConfigManager, ModelCandidate
        from ragent.infra.ai.model_selector import ModelSelector
        from ragent.infra.ai.routing_executor import RoutingExecutor

        mgr = ModelConfigManager()
        selector = ModelSelector(mgr)
        executor = RoutingExecutor(selector)

        async def my_call(candidate: ModelCandidate) -> str:
            # 执行实际的模型调用……
            return "响应文本"

        result = await executor.execute("chat", my_call)
    """

    def __init__(self, selector: ModelSelector) -> None:
        """初始化路由执行器。

        Args:
            selector: 模型选择器实例，用于获取候选列表和记录熔断器状态。
        """
        self._selector: ModelSelector = selector

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        task_type: str,
        call_fn: Callable[..., Awaitable[T]],
    ) -> T:
        """执行带降级的普通异步调用。

        执行流程：
            1. 从 selector 获取指定任务类型的候选列表
            2. 按优先级依次对每个候选调用 ``call_fn``
            3. 调用成功 → ``record_success`` 并返回结果
            4. 调用失败 → ``record_failure`` 并尝试下一个候选
            5. 所有候选均失败 → 抛出 ``RemoteException``

        Args:
            task_type: 任务类型，如 ``"chat"``、``"embedding"``、``"rerank"``。
            call_fn:   接受 ``ModelCandidate`` 并返回 ``Awaitable[T]`` 的调用函数。

        Returns:
            call_fn 的返回值。

        Raises:
            RemoteException: 当所有候选均调用失败时抛出（错误码 ``C3001``）。
        """
        candidates = self._selector.select_candidates(task_type)
        errors: list[str] = []

        for candidate in candidates:
            try:
                logger.debug(
                    "路由执行: 尝试候选模型 %s (任务类型=%s)",
                    candidate.model_name,
                    task_type,
                )
                result = await call_fn(candidate)

                # 调用成功，记录到熔断器
                self._selector.record_success(candidate.model_name)
                logger.info(
                    "路由执行: 候选模型 %s 调用成功 (任务类型=%s)",
                    candidate.model_name,
                    task_type,
                )
                return result

            except Exception as exc:
                # 调用失败，记录到熔断器
                self._selector.record_failure(candidate.model_name)
                error_msg = f"模型 {candidate.model_name} 调用失败: {exc}"
                logger.warning(error_msg)
                errors.append(error_msg)

        # 所有候选均失败
        raise RemoteException(
            error_code="C3001",
            message=f"所有模型候选均不可用 (任务类型={task_type}): {'; '.join(errors)}",
        )

    async def execute_stream(
        self,
        task_type: str,
        call_fn: Callable[..., AsyncIterator[T]],
        *,
        first_packet_timeout: float = 60.0,
    ) -> AsyncIterator[T]:
        """执行带降级的流式调用。

        与 ``execute`` 类似，但针对流式场景增加首包探测：

            1. 从 selector 获取候选列表
            2. 对每个候选调用 ``call_fn`` 获取异步迭代器
            3. 使用 ``ProbeStreamBridge`` 包装迭代器，等待首包
            4. 首包成功 → 记录成功，后续直接透传
            5. 首包超时 / 错误 → 记录失败，取消当前流并尝试下一个候选
            6. 所有候选均失败 → 抛出 ``RemoteException``

        Args:
            task_type:            任务类型。
            call_fn:              接受 ``ModelCandidate`` 并返回 ``AsyncIterator[T]`` 的调用函数。
            first_packet_timeout: 首包超时时间（秒），默认 60.0。

        Yields:
            T: call_fn 返回的异步迭代器中的每个元素。

        Raises:
            RemoteException: 当所有候选均调用失败时抛出（错误码 ``C3001``）。
        """
        candidates = self._selector.select_candidates(task_type)
        errors: list[str] = []

        for candidate in candidates:
            bridge: ProbeStreamBridge[T] | None = None
            try:
                logger.debug(
                    "路由流式执行: 尝试候选模型 %s (任务类型=%s)",
                    candidate.model_name,
                    task_type,
                )

                # 调用 call_fn 获取异步迭代器
                stream = call_fn(candidate)

                # 使用 ProbeStreamBridge 包装以实现首包探测
                bridge = ProbeStreamBridge(
                    source=stream,
                    timeout=first_packet_timeout,
                )

                # 尝试探测并透传流式数据
                async for item in bridge.probe_and_stream():
                    # 首次进入时说明首包已成功（probe_and_stream 内部处理）
                    yield item

                # 流正常结束，记录成功
                self._selector.record_success(candidate.model_name)
                logger.info(
                    "路由流式执行: 候选模型 %s 调用成功 (任务类型=%s)",
                    candidate.model_name,
                    task_type,
                )
                return  # 成功完成，退出函数

            except Exception as exc:
                # 流式调用失败，取消桥接器并记录
                if bridge is not None:
                    bridge.cancel()
                self._selector.record_failure(candidate.model_name)
                error_msg = f"模型 {candidate.model_name} 流式调用失败: {exc}"
                logger.warning(error_msg)
                errors.append(error_msg)

        # 所有候选均失败
        raise RemoteException(
            error_code="C3001",
            message=f"所有模型候选均不可用 (流式, 任务类型={task_type}): {'; '.join(errors)}",
        )
