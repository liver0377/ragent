"""流式首包探测桥接器模块 —— 包装异步迭代器，实现首包超时检测。

核心职责：
    1. 将任意 ``AsyncIterator`` 包装为支持首包超时探测的桥接器
    2. 后台协程消费源迭代器，通过 ``asyncio.Queue`` 缓冲数据
    3. 首包到达后，后续数据直接透传给消费者
    4. 首包超时或源迭代器异常时，取消后台任务并向上传播错误

典型使用场景：
    - 流式 LLM 调用的首 token 超时检测
    - 配合 ``RoutingExecutor.execute_stream`` 实现流式降级
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

# 泛型类型变量，表示迭代器元素的类型
T = TypeVar("T")

# 队列哨兵值：标记迭代结束
_SENTINEL: object = object()


class ProbeStreamBridge(Generic[T]):
    """流式首包探测桥接器。

    工作流程：
        1. 创建内部 ``asyncio.Queue`` 用于缓冲数据
        2. 后台协程（``asyncio.Task``）消费源迭代器，将元素推入队列
        3. 等待第一个元素（带超时）：
           - 首包成功 → yield 首个元素，然后继续消费队列中的剩余元素
           - 首包超时 → 取消后台任务，抛出 ``TimeoutError``
           - 源迭代器异常 → 取消后台任务，传播原始异常

    使用示例::

        async def my_stream() -> AsyncIterator[str]:
            for token in ["你好", "世界"]:
                yield token

        bridge = ProbeStreamBridge(my_stream(), timeout=10.0)
        async for item in bridge.probe_and_stream():
            print(item)
    """

    def __init__(
        self,
        source: AsyncIterator[T],
        timeout: float = 60.0,
    ) -> None:
        """初始化流式首包探测桥接器。

        Args:
            source:  源异步迭代器。
            timeout: 首包超时时间（秒），默认 60.0。
        """
        self._source: AsyncIterator[T] = source
        self._timeout: float = timeout
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._cancelled: bool = False

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    async def probe_and_stream(self) -> AsyncIterator[T]:
        """探测首包并透传后续数据。

        首包到达后，后续数据直接从内部队列透传给消费者。
        当源迭代器结束时（正常结束或异常），桥接器也会相应终止。

        Yields:
            T: 源迭代器的每个元素。

        Raises:
            TimeoutError: 首包超时时抛出。
            Exception:    源迭代器抛出的原始异常。
        """
        # 启动后台协程消费源迭代器
        self._task = asyncio.create_task(
            self._consume_source(),
            name="probe-stream-bridge-consumer",
        )

        try:
            # 等待首包（带超时）
            try:
                first_item = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                # 首包超时，取消后台任务
                self._cancel_task()
                logger.warning(
                    "首包探测超时 (%.1fs)，已取消流式传输",
                    self._timeout,
                )
                raise TimeoutError(
                    f"首包探测超时: 在 {self._timeout}s 内未收到首个数据包"
                ) from None

            # 检查首包是否为异常标记
            if isinstance(first_item, _StreamError):
                self._cancel_task()
                raise first_item.exc

            # 检查首包是否为迭代结束哨兵（空源）
            if first_item is _SENTINEL:
                logger.debug("源迭代器为空，无数据产出")
                return

            # 首包成功，yield 第一个元素
            logger.debug("首包探测成功，开始透传流式数据")
            yield first_item  # type: ignore[misc]

            # 继续消费队列中的剩余元素
            while True:
                item = await self._queue.get()
                if item is _SENTINEL:
                    # 源迭代器正常结束
                    break
                if isinstance(item, _StreamError):
                    # 源迭代器抛出异常
                    raise item.exc
                yield item  # type: ignore[misc]

        finally:
            # 确保后台任务被清理
            self._cancel_task()

    def cancel(self) -> None:
        """取消流式传输。

        外部调用方可在任意时刻调用此方法来终止流式传输。
        取消后，后台消费协程将被中止，不再产出新数据。
        """
        self._cancelled = True
        self._cancel_task()
        logger.debug("流式传输已被外部取消")

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #

    async def _consume_source(self) -> None:
        """后台协程 —— 消费源迭代器并将元素推入内部队列。

        正常结束时将哨兵 ``_SENTINEL`` 推入队列；
        异常时将 ``_StreamError`` 推入队列。
        """
        try:
            async for item in self._source:
                if self._cancelled:
                    # 已被外部取消，停止消费
                    break
                await self._queue.put(item)
            # 正常结束，放入哨兵
            await self._queue.put(_SENTINEL)
        except Exception as exc:
            # 源迭代器异常，包装后放入队列
            logger.warning("源迭代器异常: %s", exc)
            await self._queue.put(_StreamError(exc))

    def _cancel_task(self) -> None:
        """取消后台消费任务（如果存在且仍在运行）。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            logger.debug("已取消后台消费任务")


class _StreamError:
    """内部辅助类 —— 用于将源迭代器的异常通过队列传递。

    Attributes:
        exc: 源迭代器抛出的原始异常。
    """

    __slots__ = ("exc",)

    def __init__(self, exc: Exception) -> None:
        self.exc: Exception = exc
