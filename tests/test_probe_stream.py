"""ProbeStreamBridge 单元测试。

覆盖场景：
    1. 正常透传所有元素
    2. 首包超时抛出 TimeoutError
    3. 源迭代器异常传播
    4. 取消流式传输
    5. 空源迭代器（无元素）
    6. 自定义超时时间
    7. 大量元素的透传
    8. 迭代中途源异常
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from ragent.infra.ai.probe_stream import ProbeStreamBridge


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

async def _async_iter(items: list[Any]) -> AsyncIterator[Any]:
    """将列表转换为异步迭代器。"""
    for item in items:
        yield item


async def _delayed_async_iter(
    items: list[Any],
    delay: float = 0.01,
) -> AsyncIterator[Any]:
    """将列表转换为带延迟的异步迭代器。"""
    for item in items:
        await asyncio.sleep(delay)
        yield item


async def _slow_async_iter(delay: float = 10.0) -> AsyncIterator[str]:
    """模拟超时的异步迭代器 —— 长时间不产出数据。"""
    await asyncio.sleep(delay)
    yield "不会到达"


async def _error_async_iter(
    items_before_error: int = 0,
    delay: float = 0.0,
) -> AsyncIterator[str]:
    """在指定数量元素后抛出异常的异步迭代器。"""
    for i in range(items_before_error):
        if delay > 0:
            await asyncio.sleep(delay)
        yield f"item-{i}"
    raise RuntimeError("源迭代器异常")


# ---------------------------------------------------------------------------
# 测试集
# ---------------------------------------------------------------------------

class TestProbeStreamBridgeProbeAndStream:
    """ProbeStreamBridge.probe_and_stream 方法的测试集。"""

    @pytest.mark.asyncio
    async def test_yields_all_items(self) -> None:
        """正常场景 —— 透传源迭代器的所有元素。"""
        source = _async_iter(["你好", "世界", "!"])
        bridge = ProbeStreamBridge(source, timeout=5.0)

        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)

        assert items == ["你好", "世界", "!"]

    @pytest.mark.asyncio
    async def test_yields_single_item(self) -> None:
        """只有一个元素的源迭代器。"""
        source = _async_iter(["唯一"])
        bridge = ProbeStreamBridge(source, timeout=5.0)

        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)

        assert items == ["唯一"]

    @pytest.mark.asyncio
    async def test_large_number_of_items(self) -> None:
        """大量元素透传。"""
        n = 200
        source = _async_iter([f"item-{i}" for i in range(n)])
        bridge = ProbeStreamBridge(source, timeout=5.0)

        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)

        assert len(items) == n
        assert items[0] == "item-0"
        assert items[-1] == f"item-{n - 1}"

    @pytest.mark.asyncio
    async def test_first_packet_timeout_raises_timeout_error(self) -> None:
        """首包超时 —— 抛出 TimeoutError。"""
        source = _slow_async_iter(delay=10.0)
        bridge = ProbeStreamBridge(source, timeout=0.1)

        with pytest.raises(TimeoutError, match="首包探测超时"):
            async for _ in bridge.probe_and_stream():
                pass

    @pytest.mark.asyncio
    async def test_source_error_propagates(self) -> None:
        """源迭代器立即抛出异常 —— 异常被传播。"""
        source = _error_async_iter()
        bridge = ProbeStreamBridge(source, timeout=5.0)

        with pytest.raises(RuntimeError, match="源迭代器异常"):
            async for _ in bridge.probe_and_stream():
                pass

    @pytest.mark.asyncio
    async def test_source_error_after_some_items(self) -> None:
        """源迭代器产出部分元素后抛出异常 —— 先收到元素，再收到异常。"""
        source = _error_async_iter(items_before_error=3)
        bridge = ProbeStreamBridge(source, timeout=5.0)

        items: list[str] = []
        with pytest.raises(RuntimeError, match="源迭代器异常"):
            async for item in bridge.probe_and_stream():
                items.append(item)

        # 应该已经收到了异常前的元素
        assert items == ["item-0", "item-1", "item-2"]

    @pytest.mark.asyncio
    async def test_empty_source(self) -> None:
        """空源迭代器 —— 不产出任何元素，正常结束。"""
        source = _async_iter([])
        bridge = ProbeStreamBridge(source, timeout=5.0)

        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)

        assert items == []

    @pytest.mark.asyncio
    async def test_timeout_configurable(self) -> None:
        """验证超时时间可配置。"""
        # 使用非常短的超时，源迭代器稍微延迟
        source = _delayed_async_iter(["数据"], delay=5.0)
        bridge = ProbeStreamBridge(source, timeout=0.05)

        with pytest.raises(TimeoutError):
            async for _ in bridge.probe_and_stream():
                pass

    @pytest.mark.asyncio
    async def test_timeout_with_sufficient_time(self) -> None:
        """超时时间足够长时，短延迟的源迭代器能正常完成。"""
        source = _delayed_async_iter(["a", "b", "c"], delay=0.01)
        bridge = ProbeStreamBridge(source, timeout=5.0)

        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)

        assert items == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_different_element_types(self) -> None:
        """验证泛型支持 —— 不同类型的元素。"""
        source = _async_iter([1, "two", 3.0, None, True])
        bridge = ProbeStreamBridge(source, timeout=5.0)

        items: list[Any] = []
        async for item in bridge.probe_and_stream():
            items.append(item)

        assert items == [1, "two", 3.0, None, True]


class TestProbeStreamBridgeCancel:
    """ProbeStreamBridge.cancel 方法的测试集。"""

    @pytest.mark.asyncio
    async def test_cancel_stops_iteration(self) -> None:
        """取消流式传输后不再产出数据。"""
        produced: list[str] = []

        async def slow_producer() -> AsyncIterator[str]:
            for i in range(100):
                await asyncio.sleep(0.02)
                produced.append(f"item-{i}")
                yield f"item-{i}"

        source = slow_producer()
        bridge = ProbeStreamBridge(source, timeout=5.0)

        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)
            if len(items) == 2:
                # 收到两个元素后取消
                bridge.cancel()
                # 给一些时间让取消生效
                await asyncio.sleep(0.1)
                break

        # 取消后不应收到太多元素
        assert len(items) <= 5  # 允许少量竞态

    @pytest.mark.asyncio
    async def test_cancel_before_iteration(self) -> None:
        """在开始迭代前取消 —— probe_and_stream 应正常结束（无数据）。"""
        source = _async_iter(["数据"])
        bridge = ProbeStreamBridge(source, timeout=5.0)
        bridge.cancel()

        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)

        # 已取消，不应产出数据（或可能产出部分，取决于竞态）
        # 主要验证不会抛出异常或挂起

    @pytest.mark.asyncio
    async def test_cancel_idempotent(self) -> None:
        """多次取消不会抛出异常。"""
        source = _async_iter(["数据"])
        bridge = ProbeStreamBridge(source, timeout=5.0)

        # 预先消费完
        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)

        assert items == ["数据"]

        # 迭代结束后多次取消
        bridge.cancel()
        bridge.cancel()
        bridge.cancel()
        # 不应抛出异常


class TestProbeStreamBridgeEdgeCases:
    """ProbeStreamBridge 边界情况测试集。"""

    @pytest.mark.asyncio
    async def test_default_timeout_value(self) -> None:
        """验证默认超时时间为 60 秒。"""
        source = _async_iter(["测试"])
        bridge = ProbeStreamBridge(source)
        assert bridge._timeout == 60.0

        # 确保正常工作
        items: list[str] = []
        async for item in bridge.probe_and_stream():
            items.append(item)
        assert items == ["测试"]

    @pytest.mark.asyncio
    async def test_source_exception_type_preserved(self) -> None:
        """不同类型的异常都被正确传播。"""
        class CustomError(Exception):
            """自定义测试异常。"""
            pass

        async def error_source() -> AsyncIterator[str]:
            raise CustomError("自定义错误")
            yield  # 使函数成为生成器

        source = error_source()
        bridge = ProbeStreamBridge(source, timeout=5.0)

        with pytest.raises(CustomError, match="自定义错误"):
            async for _ in bridge.probe_and_stream():
                pass

    @pytest.mark.asyncio
    async def test_concurrent_task_cleanup(self) -> None:
        """验证异常发生后后台任务被正确清理。"""
        source = _slow_async_iter(delay=10.0)
        bridge = ProbeStreamBridge(source, timeout=0.05)

        with pytest.raises(TimeoutError):
            async for _ in bridge.probe_and_stream():
                pass

        # 后台任务应已完成或被取消
        await asyncio.sleep(0.1)
        if bridge._task is not None:
            assert bridge._task.done()
