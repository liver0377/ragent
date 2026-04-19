"""
分布式限流排队器单元测试 —— 全部 Redis 调用均使用 mock

覆盖范围：
    - RateLimitResult 数据类
    - RateLimiter 初始化与参数校验
    - acquire / release 流程
    - Lua 脚本位置判断逻辑
    - wait_for_turn SSE 事件结构
    - 排队位置查询
    - 超时处理
    - Semaphore 集成
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragent.common.exceptions import ClientException, ServiceException
from ragent.common.sse import SSEEvent
from ragent.concurrency.rate_limiter import (
    RateLimiter,
    RateLimitResult,
    _CHECK_POSITION_SCRIPT,
)


# ---------------------------------------------------------------------------
#  RateLimitResult 数据类测试
# ---------------------------------------------------------------------------


class TestRateLimitResult:
    """RateLimitResult 数据类测试。"""

    def test_ready_result(self) -> None:
        """position=0 时 is_ready 应为 True。"""
        result = RateLimitResult(position=0, request_id="abc123", is_ready=True)
        assert result.position == 0
        assert result.is_ready is True
        assert result.request_id == "abc123"

    def test_queued_result(self) -> None:
        """position>0 时 is_ready 应为 False。"""
        result = RateLimitResult(position=3, request_id="def456", is_ready=False)
        assert result.position == 3
        assert result.is_ready is False

    def test_is_ready_auto_correction(self) -> None:
        """is_ready 应根据 position 自动修正。"""
        # position=0 但传入 is_ready=False → 自动修正为 True
        result = RateLimitResult(position=0, request_id="test1", is_ready=False)
        assert result.is_ready is True

        # position>0 但传入 is_ready=True → 自动修正为 False
        result2 = RateLimitResult(position=5, request_id="test2", is_ready=True)
        assert result2.is_ready is False

    def test_frozen(self) -> None:
        """frozen=True 不允许修改属性。"""
        result = RateLimitResult(position=0, request_id="xyz", is_ready=True)
        with pytest.raises(AttributeError):
            result.position = 1  # type: ignore[misc]

    def test_equality(self) -> None:
        """相同字段值的结果应相等。"""
        r1 = RateLimitResult(position=0, request_id="abc", is_ready=True)
        r2 = RateLimitResult(position=0, request_id="abc", is_ready=True)
        assert r1 == r2


# ---------------------------------------------------------------------------
#  RateLimiter 初始化测试
# ---------------------------------------------------------------------------


class TestRateLimiterInit:
    """RateLimiter 初始化与参数校验测试。"""

    def test_default_params(self) -> None:
        """默认参数应正确设置。"""
        limiter = RateLimiter()
        assert limiter._max_concurrent == 5
        assert limiter._queue_key_prefix == "ragent:queue"
        assert limiter._notify_channel_prefix == "ragent:notify"
        assert limiter._semaphore_timeout == 300.0
        assert limiter._queue_ttl == 600
        assert limiter._poll_interval == 1.0

    def test_custom_params(self) -> None:
        """自定义参数应正确设置。"""
        limiter = RateLimiter(
            max_concurrent=10,
            queue_key_prefix="custom:queue",
            notify_channel_prefix="custom:notify",
            semaphore_timeout=60.0,
            queue_ttl=300,
            poll_interval=0.5,
        )
        assert limiter._max_concurrent == 10
        assert limiter._queue_key_prefix == "custom:queue"
        assert limiter._notify_channel_prefix == "custom:notify"
        assert limiter._semaphore_timeout == 60.0
        assert limiter._queue_ttl == 300
        assert limiter._poll_interval == 0.5

    def test_invalid_max_concurrent(self) -> None:
        """max_concurrent <= 0 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="max_concurrent"):
            RateLimiter(max_concurrent=0)
        with pytest.raises(ValueError, match="max_concurrent"):
            RateLimiter(max_concurrent=-1)

    def test_invalid_semaphore_timeout(self) -> None:
        """semaphore_timeout <= 0 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="semaphore_timeout"):
            RateLimiter(semaphore_timeout=0)
        with pytest.raises(ValueError, match="semaphore_timeout"):
            RateLimiter(semaphore_timeout=-1)

    def test_invalid_poll_interval(self) -> None:
        """poll_interval <= 0 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="poll_interval"):
            RateLimiter(poll_interval=0)

    def test_semaphore_initialized(self) -> None:
        """应正确初始化 asyncio.Semaphore。"""
        limiter = RateLimiter(max_concurrent=3)
        assert limiter._semaphore._value == 3


# ---------------------------------------------------------------------------
#  辅助函数测试
# ---------------------------------------------------------------------------


class TestHelperMethods:
    """内部辅助方法测试。"""

    def test_queue_key(self) -> None:
        """应正确构造队列键名。"""
        limiter = RateLimiter(queue_key_prefix="ragent:queue")
        assert limiter._queue_key(42) == "ragent:queue:42"
        assert limiter._queue_key("user_abc") == "ragent:queue:user_abc"

    def test_notify_channel(self) -> None:
        """应正确构造通知频道名。"""
        limiter = RateLimiter(notify_channel_prefix="ragent:notify")
        assert limiter._notify_channel(42) == "ragent:notify:42"
        assert limiter._notify_channel("user_abc") == "ragent:notify:user_abc"


# ---------------------------------------------------------------------------
#  acquire / release 测试（mock Redis）
# ---------------------------------------------------------------------------


def _make_mock_redis() -> AsyncMock:
    """创建模拟 Redis 客户端。"""
    redis = AsyncMock()
    redis.zadd = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.zrem = AsyncMock(return_value=1)
    redis.publish = AsyncMock(return_value=1)
    redis.eval = AsyncMock(return_value=0)  # 默认返回 0（就绪）
    redis.evalsha = AsyncMock(return_value=0)
    return redis


def _patch_get_redis(mock_redis: AsyncMock):
    """返回一个 patch 上下文管理器，替换 _get_redis 返回 mock_redis。"""

    def _fake_get_redis(self_inner):
        return mock_redis

    return patch.object(RateLimiter, "_get_redis", _fake_get_redis)


class TestAcquire:
    """acquire 方法测试。"""

    @pytest.mark.asyncio
    async def test_acquire_ready(self) -> None:
        """position=0 时应返回就绪结果。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=0)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            result = await limiter.acquire(user_id=42)

        assert result.position == 0
        assert result.is_ready is True
        assert len(result.request_id) == 32  # UUID hex

        # 验证 Redis 调用
        mock_redis.zadd.assert_called_once()
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_queued(self) -> None:
        """position>0 时应返回排队结果。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=3)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            result = await limiter.acquire(user_id=42)

        assert result.position == 3
        assert result.is_ready is False

    @pytest.mark.asyncio
    async def test_acquire_zadd_params(self) -> None:
        """应正确传递 zadd 参数。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=0)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            result = await limiter.acquire(user_id=42)

        # 检查 zadd 调用参数
        call_args = mock_redis.zadd.call_args
        queue_key = call_args[0][0]
        mapping = call_args[0][1]
        assert queue_key == "ragent:queue:42"
        assert len(mapping) == 1
        # 分数应为时间戳（float）
        score = list(mapping.values())[0]
        assert isinstance(score, float)
        assert score > 0

    @pytest.mark.asyncio
    async def test_acquire_expire(self) -> None:
        """应设置 ZSET 过期时间。"""
        mock_redis = _make_mock_redis()

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5, queue_ttl=300)
            await limiter.acquire(user_id=1)

        mock_redis.expire.assert_called_once_with("ragent:queue:1", 300)

    @pytest.mark.asyncio
    async def test_acquire_string_user_id(self) -> None:
        """应支持字符串类型的 user_id。"""
        mock_redis = _make_mock_redis()

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter()
            result = await limiter.acquire(user_id="user_abc")

        assert result.request_id  # 非空
        call_args = mock_redis.zadd.call_args
        assert call_args[0][0] == "ragent:queue:user_abc"


class TestRelease:
    """release 方法测试。"""

    @pytest.mark.asyncio
    async def test_release_success(self) -> None:
        """成功释放应调用 ZREM + publish。"""
        mock_redis = _make_mock_redis()
        mock_redis.zrem = AsyncMock(return_value=1)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            # 先消耗一个信号量
            await limiter._semaphore.acquire()
            await limiter.release(user_id=42, request_id="abc123")

        mock_redis.zrem.assert_called_once_with("ragent:queue:42", "abc123")
        mock_redis.publish.assert_called_once_with("ragent:notify:42", "release")

    @pytest.mark.asyncio
    async def test_release_not_in_queue(self) -> None:
        """请求不在队列中时应安全处理。"""
        mock_redis = _make_mock_redis()
        mock_redis.zrem = AsyncMock(return_value=0)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            await limiter._semaphore.acquire()
            await limiter.release(user_id=42, request_id="nonexistent")

        # zrem 应仍然被调用
        mock_redis.zrem.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_semaphore_overflow(self) -> None:
        """Semaphore 计数已满时释放不应抛出异常。"""
        mock_redis = _make_mock_redis()

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            # 不先 acquire，直接 release → Semaphore 计数溢出
            await limiter.release(user_id=42, request_id="abc123")

        # 不应抛出异常

    @pytest.mark.asyncio
    async def test_release_publish_failure(self) -> None:
        """publish 失败不应阻塞主流程。"""
        mock_redis = _make_mock_redis()
        mock_redis.publish = AsyncMock(side_effect=Exception("PubSub error"))

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            await limiter._semaphore.acquire()
            # 不应抛出异常
            await limiter.release(user_id=42, request_id="abc123")


# ---------------------------------------------------------------------------
#  Lua 脚本逻辑测试
# ---------------------------------------------------------------------------


class TestLuaScript:
    """Lua 位置检查脚本逻辑测试。"""

    def test_script_content(self) -> None:
        """Lua 脚本应包含核心逻辑。"""
        assert "ZRANK" in _CHECK_POSITION_SCRIPT
        assert "max_concurrent" in _CHECK_POSITION_SCRIPT
        assert "return -1" in _CHECK_POSITION_SCRIPT
        assert "return 0" in _CHECK_POSITION_SCRIPT
        assert "return rank" in _CHECK_POSITION_SCRIPT

    @pytest.mark.asyncio
    async def test_check_position_ready(self) -> None:
        """rank=0（在窗口内）应返回 0。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=0)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            pos = await limiter._check_position(mock_redis, "key", "member")

        assert pos == 0

    @pytest.mark.asyncio
    async def test_check_position_queued(self) -> None:
        """rank>=max_concurrent 应返回排队位置。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=5)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            pos = await limiter._check_position(mock_redis, "key", "member")

        assert pos == 5

    @pytest.mark.asyncio
    async def test_check_position_not_found(self) -> None:
        """请求不在队列中应返回 -1。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=-1)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            pos = await limiter._check_position(mock_redis, "key", "member")

        assert pos == -1

    @pytest.mark.asyncio
    async def test_check_position_evalsha_fallback(self) -> None:
        """EVALSHA 失败时应回退到 EVAL。"""
        mock_redis = _make_mock_redis()
        # evalsha 失败，eval 成功
        mock_redis.evalsha = AsyncMock(side_effect=Exception("NOSCRIPT"))
        mock_redis.eval = AsyncMock(return_value=0)

        limiter = RateLimiter(max_concurrent=5)
        limiter._lua_sha = "some_sha"

        pos = await limiter._check_position(mock_redis, "key", "member")

        assert pos == 0
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_position_evalsha_success(self) -> None:
        """EVALSHA 成功时应直接使用。"""
        mock_redis = _make_mock_redis()
        mock_redis.evalsha = AsyncMock(return_value=0)

        limiter = RateLimiter(max_concurrent=5)
        limiter._lua_sha = "some_sha"

        pos = await limiter._check_position(mock_redis, "key", "member")

        assert pos == 0
        mock_redis.evalsha.assert_called_once()
        mock_redis.eval.assert_not_called()


# ---------------------------------------------------------------------------
#  get_queue_position 测试
# ---------------------------------------------------------------------------


class TestGetQueuePosition:
    """排队位置查询测试。"""

    @pytest.mark.asyncio
    async def test_position_zero(self) -> None:
        """正在执行的请求应返回 0。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=0)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            pos = await limiter.get_queue_position(user_id=42, request_id="abc")

        assert pos == 0

    @pytest.mark.asyncio
    async def test_position_queued(self) -> None:
        """排队中的请求应返回 >0。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=2)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            pos = await limiter.get_queue_position(user_id=42, request_id="abc")

        assert pos == 2

    @pytest.mark.asyncio
    async def test_position_not_found(self) -> None:
        """不存在的请求应返回 -1。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=-1)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            pos = await limiter.get_queue_position(user_id=42, request_id="nonexistent")

        assert pos == -1


# ---------------------------------------------------------------------------
#  wait_for_turn 测试
# ---------------------------------------------------------------------------


class TestWaitForTurn:
    """wait_for_turn SSE 事件流测试。"""

    @pytest.mark.asyncio
    async def test_immediate_ready(self) -> None:
        """已就绪时应立即返回 processing 事件。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=0)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            events = []
            async for event in limiter.wait_for_turn(user_id=42):
                events.append(event)

        assert len(events) == 1
        assert events[0].event == "queue_status"
        data = json.loads(events[0].data)
        assert data["status"] == "processing"
        assert data["position"] == 0
        assert "request_id" in data

    @pytest.mark.asyncio
    async def test_queued_then_ready(self) -> None:
        """排队后变为就绪应先推送 waiting 再推送 processing。"""
        call_count = 0

        async def mock_eval(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return 2  # acquire 时排队
            elif call_count <= 2:
                return 1  # 第一次轮询
            else:
                return 0  # 就绪

        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(side_effect=mock_eval)
        mock_redis.evalsha = AsyncMock(side_effect=mock_eval)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5, poll_interval=0.01)
            events = []
            async for event in limiter.wait_for_turn(user_id=42, timeout=5.0):
                events.append(event)

        # 应至少有一个 waiting 和一个 processing
        statuses = [json.loads(e.data)["status"] for e in events]
        assert "processing" in statuses
        # 最后一个应是 processing
        assert statuses[-1] == "processing"

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """超时应抛出 ClientException。"""
        mock_redis = _make_mock_redis()
        # 始终返回排队状态
        mock_redis.eval = AsyncMock(return_value=5)
        mock_redis.evalsha = AsyncMock(return_value=5)
        mock_redis.zrem = AsyncMock(return_value=1)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5, poll_interval=0.01)
            events = []
            with pytest.raises(ClientException) as exc_info:
                async for event in limiter.wait_for_turn(
                    user_id=42, timeout=0.05
                ):
                    events.append(event)

        assert "超时" in exc_info.value.message
        assert exc_info.value.error_code == "A1010"
        # 超时后应清理队列
        mock_redis.zrem.assert_called()

    @pytest.mark.asyncio
    async def test_invalid_timeout(self) -> None:
        """timeout <= 0 应抛出 ValueError。"""
        limiter = RateLimiter()
        with pytest.raises(ValueError, match="timeout"):
            async for _ in limiter.wait_for_turn(user_id=42, timeout=0):
                pass

    @pytest.mark.asyncio
    async def test_sse_event_structure(self) -> None:
        """SSE 事件应符合预期结构。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=0)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5)
            async for event in limiter.wait_for_turn(user_id=42):
                assert isinstance(event, SSEEvent)
                assert event.event == "queue_status"
                data = json.loads(event.data)
                assert "position" in data
                assert "status" in data
                assert isinstance(data["position"], int)
                assert isinstance(data["status"], str)

    @pytest.mark.asyncio
    async def test_request_lost_and_requeue(self) -> None:
        """请求丢失后应自动重新入队。"""
        call_count = 0

        async def mock_eval(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 0  # acquire 就绪
            elif call_count == 2:
                return -1  # get_queue_position 发现丢失
            elif call_count == 3:
                return 0  # 重新 acquire 就绪
            else:
                return 0  # check_position 就绪

        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(side_effect=mock_eval)
        mock_redis.evalsha = AsyncMock(side_effect=mock_eval)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=5, poll_interval=0.01)
            events = []
            async for event in limiter.wait_for_turn(user_id=42, timeout=5.0):
                events.append(event)

        # 最终应获得 processing 事件
        last_data = json.loads(events[-1].data)
        assert last_data["status"] == "processing"


# ---------------------------------------------------------------------------
#  Semaphore 集成测试
# ---------------------------------------------------------------------------


class TestSemaphoreIntegration:
    """Semaphore 集成测试。"""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self) -> None:
        """Semaphore 应正确限制并发数。"""
        limiter = RateLimiter(max_concurrent=2)
        assert limiter._semaphore._value == 2

        # 获取一个许可
        acquired = await limiter._try_acquire_semaphore("test1")
        assert acquired is True
        assert limiter._semaphore._value == 1

        # 获取第二个许可
        acquired = await limiter._try_acquire_semaphore("test2")
        assert acquired is True
        assert limiter._semaphore._value == 0

    @pytest.mark.asyncio
    async def test_semaphore_timeout(self) -> None:
        """Semaphore 获取超时应返回 False。"""
        limiter = RateLimiter(max_concurrent=1, semaphore_timeout=0.05)

        # 先消耗唯一的许可
        await limiter._semaphore.acquire()
        assert limiter._semaphore._value == 0

        # 尝试获取应超时
        acquired = await limiter._try_acquire_semaphore("test_timeout")
        assert acquired is False

    @pytest.mark.asyncio
    async def test_semaphore_release_increments(self) -> None:
        """释放 Semaphore 应增加计数。"""
        limiter = RateLimiter(max_concurrent=2)
        await limiter._semaphore.acquire()
        assert limiter._semaphore._value == 1

        limiter._semaphore.release()
        assert limiter._semaphore._value == 2


# ---------------------------------------------------------------------------
#  Redis 未初始化测试
# ---------------------------------------------------------------------------


class TestRedisNotInitialized:
    """Redis 未初始化时的错误处理测试。"""

    @pytest.mark.asyncio
    async def test_acquire_raises_when_redis_not_init(self) -> None:
        """Redis 未初始化时 acquire 应抛出 ServiceException。"""
        with patch(
            "ragent.concurrency.rate_limiter.RateLimiter._get_redis",
            side_effect=ServiceException(error_code="B2101", message="Redis 未初始化"),
        ):
            limiter = RateLimiter()
            with pytest.raises(ServiceException) as exc_info:
                await limiter.acquire(user_id=42)
            assert exc_info.value.error_code == "B2101"

    @pytest.mark.asyncio
    async def test_release_raises_when_redis_not_init(self) -> None:
        """Redis 未初始化时 release 应抛出 ServiceException。"""
        with patch(
            "ragent.concurrency.rate_limiter.RateLimiter._get_redis",
            side_effect=ServiceException(error_code="B2101", message="Redis 未初始化"),
        ):
            limiter = RateLimiter()
            with pytest.raises(ServiceException):
                await limiter.release(user_id=42, request_id="abc")


# ---------------------------------------------------------------------------
#  _cleanup_on_timeout 测试
# ---------------------------------------------------------------------------


class TestCleanupOnTimeout:
    """超时清理测试。"""

    @pytest.mark.asyncio
    async def test_cleanup_removes_from_queue(self) -> None:
        """超时清理应从队列中移除请求。"""
        mock_redis = _make_mock_redis()

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter()
            await limiter._cleanup_on_timeout(user_id=42, request_id="abc")

        mock_redis.zrem.assert_called_once_with("ragent:queue:42", "abc")

    @pytest.mark.asyncio
    async def test_cleanup_handles_failure(self) -> None:
        """清理失败不应抛出异常。"""
        mock_redis = _make_mock_redis()
        mock_redis.zrem = AsyncMock(side_effect=Exception("Redis error"))

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter()
            # 不应抛出异常
            await limiter._cleanup_on_timeout(user_id=42, request_id="abc")


# ---------------------------------------------------------------------------
#  完整流程集成测试
# ---------------------------------------------------------------------------


class TestFullFlow:
    """完整流程集成测试（mock Redis）。"""

    @pytest.mark.asyncio
    async def test_acquire_release_cycle(self) -> None:
        """完整的 acquire → release 周期。"""
        mock_redis = _make_mock_redis()
        mock_redis.eval = AsyncMock(return_value=0)

        with _patch_get_redis(mock_redis):
            limiter = RateLimiter(max_concurrent=2)

            # 获取许可
            result = await limiter.acquire(user_id=1)
            assert result.is_ready is True
            assert limiter._semaphore._value == 2  # Semaphore 还未获取

        # wait_for_turn 会获取 Semaphore
        with _patch_get_redis(mock_redis):
            events = []
            async for event in limiter.wait_for_turn(user_id=1, timeout=5.0):
                events.append(event)
            assert limiter._semaphore._value == 1

        # 释放
        with _patch_get_redis(mock_redis):
            request_id = json.loads(events[-1].data)["request_id"]
            await limiter.release(user_id=1, request_id=request_id)
            assert limiter._semaphore._value == 2
