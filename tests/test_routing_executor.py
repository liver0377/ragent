"""RoutingExecutor 单元测试。

覆盖场景：
    1. 首个候选调用成功
    2. 首个候选失败，降级到第二个候选成功
    3. 所有候选均失败 → 抛出 RemoteException
    4. 成功时正确记录 record_success
    5. 失败时正确记录 record_failure
    6. 候选列表为空 → 抛出异常
    7. 流式调用成功
    8. 流式调用首包超时降级
    9. 流式调用源异常降级
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragent.common.exceptions import RemoteException
from ragent.infra.ai.models import ModelCandidate
from ragent.infra.ai.routing_executor import RoutingExecutor


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

def _make_candidate(name: str, priority: int = 0) -> ModelCandidate:
    """创建测试用的 ModelCandidate 实例。"""
    return ModelCandidate(
        model_name=name,
        provider="test",
        priority=priority,
        timeout=5.0,
    )


def _make_selector_mock(candidates: list[ModelCandidate]) -> MagicMock:
    """创建模拟的 ModelSelector 实例。

    Args:
        candidates: select_candidates 方法返回的候选列表。

    Returns:
        配置好 select_candidates 的 MagicMock。
    """
    selector = MagicMock()
    selector.select_candidates.return_value = candidates
    selector.record_success = MagicMock()
    selector.record_failure = MagicMock()
    return selector


# ---------------------------------------------------------------------------
# execute 测试
# ---------------------------------------------------------------------------

class TestRoutingExecutorExecute:
    """RoutingExecutor.execute 方法的测试集。"""

    @pytest.mark.asyncio
    async def test_first_candidate_succeeds(self) -> None:
        """首个候选调用成功 —— 直接返回结果，不尝试后续候选。"""
        c1 = _make_candidate("model-a")
        c2 = _make_candidate("model-b")
        selector = _make_selector_mock([c1, c2])

        call_fn = AsyncMock(return_value="响应文本")

        executor = RoutingExecutor(selector)
        result = await executor.execute("chat", call_fn)

        assert result == "响应文本"
        call_fn.assert_called_once_with(c1)
        selector.record_success.assert_called_once_with("model-a")
        selector.record_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_to_second_candidate(self) -> None:
        """首个候选失败，降级到第二个候选成功。"""
        c1 = _make_candidate("model-a")
        c2 = _make_candidate("model-b")
        selector = _make_selector_mock([c1, c2])

        call_fn = AsyncMock(side_effect=[RuntimeError("模型不可用"), "响应文本"])

        executor = RoutingExecutor(selector)
        result = await executor.execute("chat", call_fn)

        assert result == "响应文本"
        assert call_fn.call_count == 2
        selector.record_failure.assert_called_once_with("model-a")
        selector.record_success.assert_called_once_with("model-b")

    @pytest.mark.asyncio
    async def test_all_candidates_fail_raises_remote_exception(self) -> None:
        """所有候选均失败 —— 抛出 RemoteException。"""
        c1 = _make_candidate("model-a")
        c2 = _make_candidate("model-b")
        selector = _make_selector_mock([c1, c2])

        call_fn = AsyncMock(side_effect=RuntimeError("不可用"))

        executor = RoutingExecutor(selector)
        with pytest.raises(RemoteException) as exc_info:
            await executor.execute("chat", call_fn)

        assert exc_info.value.error_code == "C3001"
        assert "model-a" in exc_info.value.message
        assert "model-b" in exc_info.value.message
        assert call_fn.call_count == 2
        assert selector.record_failure.call_count == 2
        selector.record_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_failure_on_each_failed_candidate(self) -> None:
        """验证每个失败的候选都会调用 record_failure。"""
        c1 = _make_candidate("model-a")
        c2 = _make_candidate("model-b")
        c3 = _make_candidate("model-c")
        selector = _make_selector_mock([c1, c2, c3])

        call_fn = AsyncMock(
            side_effect=[
                RuntimeError("错误1"),
                ConnectionError("错误2"),
                "最终成功",
            ]
        )

        executor = RoutingExecutor(selector)
        result = await executor.execute("chat", call_fn)

        assert result == "最终成功"
        assert selector.record_failure.call_count == 2
        selector.record_failure.assert_any_call("model-a")
        selector.record_failure.assert_any_call("model-b")
        selector.record_success.assert_called_once_with("model-c")

    @pytest.mark.asyncio
    async def test_empty_candidates_raises_remote_exception(self) -> None:
        """候选列表为空 —— select_candidates 抛出 RemoteException。"""
        selector = _make_selector_mock([])
        selector.select_candidates.side_effect = RemoteException(
            error_code="C3001",
            message="没有可用的已启用模型候选",
        )

        call_fn = AsyncMock()

        executor = RoutingExecutor(selector)
        with pytest.raises(RemoteException) as exc_info:
            await executor.execute("chat", call_fn)

        assert exc_info.value.error_code == "C3001"
        call_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_task_types(self) -> None:
        """验证 task_type 被正确传递给 select_candidates。"""
        c1 = _make_candidate("embed-model")
        selector = _make_selector_mock([c1])

        call_fn = AsyncMock(return_value=[0.1, 0.2, 0.3])

        executor = RoutingExecutor(selector)
        result = await executor.execute("embedding", call_fn)

        assert result == [0.1, 0.2, 0.3]
        selector.select_candidates.assert_called_once_with("embedding")

    @pytest.mark.asyncio
    async def test_single_candidate_success(self) -> None:
        """只有一个候选且成功的情况。"""
        c1 = _make_candidate("solo-model")
        selector = _make_selector_mock([c1])

        call_fn = AsyncMock(return_value=42)

        executor = RoutingExecutor(selector)
        result = await executor.execute("rerank", call_fn)

        assert result == 42
        call_fn.assert_called_once_with(c1)
        selector.record_success.assert_called_once_with("solo-model")


# ---------------------------------------------------------------------------
# execute_stream 测试
# ---------------------------------------------------------------------------

class TestRoutingExecutorExecuteStream:
    """RoutingExecutor.execute_stream 方法的测试集。"""

    @pytest.mark.asyncio
    async def test_stream_success(self) -> None:
        """流式调用成功 —— 透传所有元素。"""
        c1 = _make_candidate("model-a")
        selector = _make_selector_mock([c1])

        async def mock_stream(candidate: ModelCandidate) -> AsyncIterator[str]:
            yield "你好"
            yield "世界"

        executor = RoutingExecutor(selector)
        items: list[str] = []
        async for item in executor.execute_stream("chat", mock_stream):
            items.append(item)

        assert items == ["你好", "世界"]
        selector.record_success.assert_called_once_with("model-a")
        selector.record_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_fallback_on_first_packet_timeout(self) -> None:
        """首个候选首包超时，降级到第二个候选成功。"""
        c1 = _make_candidate("slow-model")
        c2 = _make_candidate("fast-model")
        selector = _make_selector_mock([c1, c2])

        call_count = 0

        async def mock_stream(candidate: ModelCandidate) -> AsyncIterator[str]:
            nonlocal call_count
            call_count += 1
            if candidate.model_name == "slow-model":
                # 模拟首包超时：永远不产出数据
                await asyncio.sleep(10.0)
                yield "不会到达"
            else:
                yield "快速响应"

        executor = RoutingExecutor(selector)
        items: list[str] = []
        async for item in executor.execute_stream(
            "chat", mock_stream, first_packet_timeout=0.1
        ):
            items.append(item)

        assert items == ["快速响应"]
        selector.record_failure.assert_called_once_with("slow-model")
        selector.record_success.assert_called_once_with("fast-model")

    @pytest.mark.asyncio
    async def test_stream_fallback_on_source_error(self) -> None:
        """首个候选源迭代器异常，降级到第二个候选成功。"""
        c1 = _make_candidate("bad-model")
        c2 = _make_candidate("good-model")
        selector = _make_selector_mock([c1, c2])

        async def mock_stream(candidate: ModelCandidate) -> AsyncIterator[str]:
            if candidate.model_name == "bad-model":
                raise ConnectionError("连接失败")
                yield  # 使函数成为生成器（不可达）
            else:
                yield "成功响应"

        executor = RoutingExecutor(selector)
        items: list[str] = []
        async for item in executor.execute_stream("chat", mock_stream):
            items.append(item)

        assert items == ["成功响应"]
        selector.record_failure.assert_called_once_with("bad-model")
        selector.record_success.assert_called_once_with("good-model")

    @pytest.mark.asyncio
    async def test_stream_all_candidates_fail(self) -> None:
        """所有候选流式调用均失败 —— 抛出 RemoteException。"""
        c1 = _make_candidate("model-a")
        c2 = _make_candidate("model-b")
        selector = _make_selector_mock([c1, c2])

        async def mock_stream(candidate: ModelCandidate) -> AsyncIterator[str]:
            raise RuntimeError(f"{candidate.model_name} 不可用")
            yield  # 使函数成为生成器

        executor = RoutingExecutor(selector)
        with pytest.raises(RemoteException) as exc_info:
            async for _ in executor.execute_stream("chat", mock_stream):
                pass

        assert exc_info.value.error_code == "C3001"
        assert selector.record_failure.call_count == 2

    @pytest.mark.asyncio
    async def test_stream_empty_source(self) -> None:
        """源迭代器不产出任何元素（空流）。"""
        c1 = _make_candidate("empty-model")
        selector = _make_selector_mock([c1])

        async def mock_stream(candidate: ModelCandidate) -> AsyncIterator[str]:
            return
            yield  # 使函数成为生成器

        executor = RoutingExecutor(selector)
        items: list[str] = []
        async for item in executor.execute_stream("chat", mock_stream):
            items.append(item)

        assert items == []
        # 空源迭代器正常结束（无首包但无异常），应记录成功
        selector.record_success.assert_called_once_with("empty-model")

    @pytest.mark.asyncio
    async def test_stream_custom_first_packet_timeout(self) -> None:
        """验证自定义首包超时参数生效。"""
        c1 = _make_candidate("model-a")
        selector = _make_selector_mock([c1])

        async def mock_stream(candidate: ModelCandidate) -> AsyncIterator[str]:
            # 短暂延迟后产出数据
            await asyncio.sleep(0.05)
            yield "数据"

        executor = RoutingExecutor(selector)
        items: list[str] = []
        async for item in executor.execute_stream(
            "chat", mock_stream, first_packet_timeout=1.0
        ):
            items.append(item)

        assert items == ["数据"]
        selector.record_success.assert_called_once_with("model-a")
