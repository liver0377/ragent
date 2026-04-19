"""Tests for ragent.common.redis_manager module."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragent.common.redis_manager import (
    DistributedLock,
    RedisManager,
    get_redis_manager,
)


# ---------------------------------------------------------------------------
# RedisManager
# ---------------------------------------------------------------------------

class TestRedisManager:
    def test_key_namespace_formatting(self):
        """_key should format as '{prefix}:{name}'."""
        rm = RedisManager()
        rm._prefix = "ragent"
        assert rm._key("user:1") == "ragent:user:1"
        assert rm._key("lock:my-lock") == "ragent:lock:my-lock"

    def test_key_custom_prefix(self):
        rm = RedisManager()
        rm._prefix = "myprefix"
        assert rm._key("test") == "myprefix:test"

    def test_singleton_get_instance(self):
        rm1 = RedisManager._get_instance()
        rm2 = RedisManager._get_instance()
        assert rm1 is rm2

    def test_get_redis_raises_before_init(self):
        rm = RedisManager()
        # New instance without init
        with pytest.raises(RuntimeError, match="尚未初始化"):
            rm.get_redis()

    def test_get_redis_returns_client_after_manual_set(self):
        rm = RedisManager()
        mock_redis = MagicMock()
        rm._redis = mock_redis
        assert rm.get_redis() is mock_redis


# ---------------------------------------------------------------------------
# get_redis_manager
# ---------------------------------------------------------------------------

class TestGetRedisManager:
    def test_returns_redis_manager_instance(self):
        rm = get_redis_manager()
        assert isinstance(rm, RedisManager)

    def test_singleton_behavior(self):
        rm1 = get_redis_manager()
        rm2 = get_redis_manager()
        assert rm1 is rm2


# ---------------------------------------------------------------------------
# RedisManager with mocked Redis operations
# ---------------------------------------------------------------------------

class TestRedisManagerWithMock:
    def _make_manager(self):
        """Create a RedisManager with a mocked Redis client."""
        rm = RedisManager()
        rm._prefix = "ragent"
        rm._redis = AsyncMock()
        rm._pool = MagicMock()
        return rm

    @pytest.mark.asyncio
    async def test_get(self):
        rm = self._make_manager()
        rm._redis.get = AsyncMock(return_value="hello")
        result = await rm.get("mykey")
        rm._redis.get.assert_called_once_with("ragent:mykey")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_set(self):
        rm = self._make_manager()
        rm._redis.set = AsyncMock(return_value=True)
        result = await rm.set("mykey", "myvalue")
        rm._redis.set.assert_called_once_with("ragent:mykey", "myvalue", ex=None, px=None, nx=False, xx=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_set_with_expiry(self):
        rm = self._make_manager()
        rm._redis.set = AsyncMock(return_value=True)
        await rm.set("mykey", "myvalue", ex=60, nx=True)
        rm._redis.set.assert_called_once_with("ragent:mykey", "myvalue", ex=60, px=None, nx=True, xx=False)

    @pytest.mark.asyncio
    async def test_delete(self):
        rm = self._make_manager()
        rm._redis.delete = AsyncMock(return_value=2)
        result = await rm.delete("key1", "key2")
        rm._redis.delete.assert_called_once_with("ragent:key1", "ragent:key2")
        assert result == 2

    @pytest.mark.asyncio
    async def test_delete_empty(self):
        rm = self._make_manager()
        result = await rm.delete()
        assert result == 0

    @pytest.mark.asyncio
    async def test_exists(self):
        rm = self._make_manager()
        rm._redis.exists = AsyncMock(return_value=1)
        result = await rm.exists("mykey")
        rm._redis.exists.assert_called_once_with("ragent:mykey")
        assert result is True

    @pytest.mark.asyncio
    async def test_expire(self):
        rm = self._make_manager()
        rm._redis.expire = AsyncMock(return_value=True)
        result = await rm.expire("mykey", 60)
        rm._redis.expire.assert_called_once_with("ragent:mykey", 60)
        assert result is True

    @pytest.mark.asyncio
    async def test_ttl(self):
        rm = self._make_manager()
        rm._redis.ttl = AsyncMock(return_value=42)
        result = await rm.ttl("mykey")
        rm._redis.ttl.assert_called_once_with("ragent:mykey")
        assert result == 42

    @pytest.mark.asyncio
    async def test_hset(self):
        rm = self._make_manager()
        rm._redis.hset = AsyncMock(return_value=1)
        result = await rm.hset("myhash", "field1", "value1")
        rm._redis.hset.assert_called_once_with("ragent:myhash", "field1", "value1")
        assert result == 1

    @pytest.mark.asyncio
    async def test_hget(self):
        rm = self._make_manager()
        rm._redis.hget = AsyncMock(return_value="value1")
        result = await rm.hget("myhash", "field1")
        rm._redis.hget.assert_called_once_with("ragent:myhash", "field1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_hgetall(self):
        rm = self._make_manager()
        rm._redis.hgetall = AsyncMock(return_value={"f1": "v1"})
        result = await rm.hgetall("myhash")
        rm._redis.hgetall.assert_called_once_with("ragent:myhash")
        assert result == {"f1": "v1"}

    @pytest.mark.asyncio
    async def test_hdel(self):
        rm = self._make_manager()
        rm._redis.hdel = AsyncMock(return_value=2)
        result = await rm.hdel("myhash", "f1", "f2")
        rm._redis.hdel.assert_called_once_with("ragent:myhash", "f1", "f2")
        assert result == 2

    @pytest.mark.asyncio
    async def test_hdel_empty(self):
        rm = self._make_manager()
        result = await rm.hdel("myhash")
        assert result == 0

    @pytest.mark.asyncio
    async def test_zadd(self):
        rm = self._make_manager()
        rm._redis.zadd = AsyncMock(return_value=1)
        result = await rm.zadd("myzset", {"member1": 1.0})
        rm._redis.zadd.assert_called_once_with("ragent:myzset", {"member1": 1.0})
        assert result == 1

    @pytest.mark.asyncio
    async def test_zrange(self):
        rm = self._make_manager()
        rm._redis.zrange = AsyncMock(return_value=["a", "b"])
        result = await rm.zrange("myzset", 0, -1)
        rm._redis.zrange.assert_called_once_with("ragent:myzset", 0, -1, withscores=False)
        assert result == ["a", "b"]

    @pytest.mark.asyncio
    async def test_zremrangebyscore(self):
        rm = self._make_manager()
        rm._redis.zremrangebyscore = AsyncMock(return_value=3)
        result = await rm.zremrangebyscore("myzset", "-inf", 100)
        rm._redis.zremrangebyscore.assert_called_once_with("ragent:myzset", "-inf", 100)
        assert result == 3

    @pytest.mark.asyncio
    async def test_ping_success(self):
        rm = self._make_manager()
        rm._redis.ping = AsyncMock(return_value=True)
        result = await rm.ping()
        assert result is True

    @pytest.mark.asyncio
    async def test_ping_failure(self):
        rm = self._make_manager()
        from redis.exceptions import RedisError
        rm._redis.ping = AsyncMock(side_effect=RedisError("connection refused"))
        result = await rm.ping()
        assert result is False

    @pytest.mark.asyncio
    async def test_close(self):
        rm = self._make_manager()
        mock_pool = rm._pool
        mock_pool.aclose = AsyncMock()
        await rm.close()
        mock_pool.aclose.assert_called_once()
        assert rm._pool is None
        assert rm._redis is None

    def test_lock_creates_distributed_lock(self):
        rm = self._make_manager()
        lock = rm.lock("test-lock", ttl=10)
        assert isinstance(lock, DistributedLock)
        # Lock key should include prefix + "lock:" + name
        assert lock.name == "ragent:lock:test-lock"


# ---------------------------------------------------------------------------
# DistributedLock (mocked Redis)
# ---------------------------------------------------------------------------

class TestDistributedLock:
    def _make_lock(self, **kwargs):
        mock_redis = AsyncMock()
        lock = DistributedLock(
            redis_client=mock_redis,
            name="test-lock",
            **kwargs,
        )
        return lock, mock_redis

    def test_name_property(self):
        lock, _ = self._make_lock()
        assert lock.name == "test-lock"

    def test_token_is_hex(self):
        lock, _ = self._make_lock()
        assert len(lock._token) == 32
        int(lock._token, 16)  # Should not raise

    def test_ttl_conversion(self):
        lock, _ = self._make_lock(ttl=10.5)
        assert lock._ttl_ms == 10500

    @pytest.mark.asyncio
    async def test_acquire_success(self):
        lock, mock_redis = self._make_lock()
        mock_redis.set = AsyncMock(return_value=True)
        result = await lock.acquire()
        assert result is True
        mock_redis.set.assert_called_once_with(
            "test-lock", lock._token, nx=True, px=lock._ttl_ms
        )

    @pytest.mark.asyncio
    async def test_acquire_fails_after_retries(self):
        lock, mock_redis = self._make_lock(retry_times=2, retry_delay=0.01)
        mock_redis.set = AsyncMock(return_value=None)
        result = await lock.acquire()
        assert result is False
        # Should have been called retry_times + 1 times
        assert mock_redis.set.call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_release_success(self):
        lock, mock_redis = self._make_lock()
        mock_redis.eval = AsyncMock(return_value=1)
        result = await lock.release()
        assert result is True

    @pytest.mark.asyncio
    async def test_release_failure(self):
        lock, mock_redis = self._make_lock()
        mock_redis.eval = AsyncMock(return_value=0)
        result = await lock.release()
        assert result is False

    @pytest.mark.asyncio
    async def test_extend_success(self):
        lock, mock_redis = self._make_lock()
        mock_redis.eval = AsyncMock(return_value=1)
        result = await lock.extend(10.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_extend_failure(self):
        lock, mock_redis = self._make_lock()
        mock_redis.eval = AsyncMock(return_value=0)
        result = await lock.extend(10.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_context_manager_acquire_and_release(self):
        lock, mock_redis = self._make_lock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=1)

        async with lock:
            # Inside context, lock should be acquired
            pass
        # After exiting, release should have been called
        mock_redis.set.assert_called_once()
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_acquire_fails_raises(self):
        from redis.exceptions import RedisError

        lock, mock_redis = self._make_lock(retry_times=0)
        mock_redis.set = AsyncMock(return_value=None)

        with pytest.raises(RedisError, match="无法获取分布式锁"):
            async with lock:
                pass

    @pytest.mark.asyncio
    async def test_blocking_timeout(self):
        lock, mock_redis = self._make_lock(
            retry_times=100,
            retry_delay=0.1,
            blocking_timeout=0.05,
        )
        mock_redis.set = AsyncMock(return_value=None)
        result = await lock.acquire()
        assert result is False
