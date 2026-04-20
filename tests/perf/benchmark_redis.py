"""
Redis 操作性能基准测试。

使用真实的 Redis 连接（localhost:6379），测试：
    1. test_benchmark_redis_set_get — 基本 SET/GET 性能
    2. test_benchmark_redis_hash — Hash 操作性能
    3. test_benchmark_redis_pipeline — Pipeline 批量操作性能
    4. test_benchmark_distributed_lock — 分布式锁获取/释放性能
"""

from __future__ import annotations

import time
import uuid

import pytest
import redis.asyncio
from redis.asyncio import ConnectionPool, Redis

from ragent.common.redis_manager import DistributedLock, RedisManager, get_redis_manager


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

ITERATIONS = 100
REDIS_URL = "redis://localhost:6379/0"
KEY_PREFIX = "ragent:bench:"


def _compute_stats(times: list[float]) -> dict[str, float]:
    """根据耗时列表计算统计指标。"""
    sorted_times = sorted(times)
    n = len(sorted_times)
    total = sum(sorted_times)
    return {
        "avg_ms": (total / n) * 1000,
        "p50_ms": sorted_times[int(n * 0.50)] * 1000,
        "p95_ms": sorted_times[int(n * 0.95)] * 1000,
        "p99_ms": sorted_times[int(n * 0.99)] * 1000,
        "min_ms": sorted_times[0] * 1000,
        "max_ms": sorted_times[-1] * 1000,
        "ops_per_sec": n / total if total > 0 else 0,
    }


def _print_stats(name: str, stats: dict[str, float]) -> None:
    """打印性能统计信息。"""
    print(f"\n{'=' * 60}")
    print(f"  Benchmark: {name}")
    print(f"{'=' * 60}")
    print(f"  Avg:   {stats['avg_ms']:.4f} ms")
    print(f"  P50:   {stats['p50_ms']:.4f} ms")
    print(f"  P95:   {stats['p95_ms']:.4f} ms")
    print(f"  P99:   {stats['p99_ms']:.4f} ms")
    print(f"  Min:   {stats['min_ms']:.4f} ms")
    print(f"  Max:   {stats['max_ms']:.4f} ms")
    print(f"  Ops/s: {stats['ops_per_sec']:.1f}")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client():
    """创建并清理 Redis 客户端。"""
    pool = ConnectionPool.from_url(REDIS_URL, decode_responses=True, max_connections=10)
    client = Redis(connection_pool=pool)
    # 验证连接
    await client.ping()
    yield client
    # 清理测试 key
    async for key in client.scan_iter(match=f"{KEY_PREFIX}*"):
        await client.delete(key)
    await pool.aclose()


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


async def test_benchmark_redis_set_get(redis_client: Redis) -> None:
    """基本 SET/GET 性能。"""
    # Warmup
    for i in range(10):
        k = f"{KEY_PREFIX}warmup:set:{i}"
        await redis_client.set(k, f"value_{i}")
        await redis_client.get(k)
        await redis_client.delete(k)

    # ---------- Benchmark: SET ----------
    times_set: list[float] = []
    for i in range(ITERATIONS):
        k = f"{KEY_PREFIX}set:{i}"
        v = f"benchmark_value_{i}"
        t0 = time.perf_counter()
        await redis_client.set(k, v)
        t1 = time.perf_counter()
        times_set.append(t1 - t0)

    stats_set = _compute_stats(times_set)
    _print_stats("Redis SET", stats_set)

    # ---------- Benchmark: GET ----------
    times_get: list[float] = []
    for i in range(ITERATIONS):
        k = f"{KEY_PREFIX}set:{i}"
        t0 = time.perf_counter()
        await redis_client.get(k)
        t1 = time.perf_counter()
        times_get.append(t1 - t0)

    stats_get = _compute_stats(times_get)
    _print_stats("Redis GET", stats_get)

    assert stats_set["avg_ms"] < 10, "Redis SET 平均耗时不应超过 10ms"
    assert stats_get["avg_ms"] < 10, "Redis GET 平均耗时不应超过 10ms"


async def test_benchmark_redis_hash(redis_client: Redis) -> None:
    """Hash 操作性能。"""
    hash_key = f"{KEY_PREFIX}hash:test"

    # ---------- Benchmark: HSET ----------
    times_hset: list[float] = []
    for i in range(ITERATIONS):
        field = f"field_{i}"
        value = f"value_{i}"
        t0 = time.perf_counter()
        await redis_client.hset(hash_key, field, value)
        t1 = time.perf_counter()
        times_hset.append(t1 - t0)

    stats_hset = _compute_stats(times_hset)
    _print_stats("Redis HSET", stats_hset)

    # ---------- Benchmark: HGET ----------
    times_hget: list[float] = []
    for i in range(ITERATIONS):
        field = f"field_{i}"
        t0 = time.perf_counter()
        await redis_client.hget(hash_key, field)
        t1 = time.perf_counter()
        times_hget.append(t1 - t0)

    stats_hget = _compute_stats(times_hget)
    _print_stats("Redis HGET", stats_hget)

    # ---------- Benchmark: HGETALL ----------
    times_hgetall: list[float] = []
    for _ in range(ITERATIONS):
        t0 = time.perf_counter()
        await redis_client.hgetall(hash_key)
        t1 = time.perf_counter()
        times_hgetall.append(t1 - t0)

    stats_hgetall = _compute_stats(times_hgetall)
    _print_stats("Redis HGETALL (100 fields)", stats_hgetall)

    assert stats_hset["avg_ms"] < 10, "Redis HSET 平均耗时不应超过 10ms"
    assert stats_hget["avg_ms"] < 10, "Redis HGET 平均耗时不应超过 10ms"
    assert stats_hgetall["avg_ms"] < 50, "Redis HGETALL 平均耗时不应超过 50ms"


async def test_benchmark_redis_pipeline(redis_client: Redis) -> None:
    """Pipeline 批量操作性能。"""
    batch_size = 100

    # ---------- Benchmark: Pipeline SET ----------
    times_pipe_set: list[float] = []
    for i in range(ITERATIONS):
        t0 = time.perf_counter()
        async with redis_client.pipeline(transaction=False) as pipe:
            for j in range(batch_size):
                k = f"{KEY_PREFIX}pipe:{i}:{j}"
                pipe.set(k, f"pipe_value_{j}")
            await pipe.execute()
        t1 = time.perf_counter()
        times_pipe_set.append(t1 - t0)

    stats_pipe_set = _compute_stats(times_pipe_set)
    _print_stats(f"Redis Pipeline SET (batch={batch_size})", stats_pipe_set)

    # ---------- Benchmark: Pipeline GET ----------
    times_pipe_get: list[float] = []
    for i in range(ITERATIONS):
        t0 = time.perf_counter()
        async with redis_client.pipeline(transaction=False) as pipe:
            for j in range(batch_size):
                k = f"{KEY_PREFIX}pipe:{i}:{j}"
                pipe.get(k)
            await pipe.execute()
        t1 = time.perf_counter()
        times_pipe_get.append(t1 - t0)

    stats_pipe_get = _compute_stats(times_pipe_get)
    _print_stats(f"Redis Pipeline GET (batch={batch_size})", stats_pipe_get)

    # Pipeline 批量操作应比单次操作高效
    ops_per_batch = stats_pipe_set["ops_per_sec"]
    single_ops_equivalent = ops_per_batch * batch_size
    assert stats_pipe_set["avg_ms"] < 100, f"Pipeline SET {batch_size} 条平均耗时不应超过 100ms"
    assert stats_pipe_get["avg_ms"] < 100, f"Pipeline GET {batch_size} 条平均耗时不应超过 100ms"


async def test_benchmark_distributed_lock(redis_client: Redis) -> None:
    """分布式锁获取/释放性能。"""
    lock_name = f"{KEY_PREFIX}lock:bench"

    # ---------- Benchmark: acquire ----------
    times_acquire: list[float] = []
    for i in range(ITERATIONS):
        lock_key = f"{lock_name}:{i}"
        lock = DistributedLock(
            redis_client,
            lock_key,
            ttl=10.0,
            retry_times=1,
            retry_delay=0.01,
        )
        t0 = time.perf_counter()
        acquired = await lock.acquire()
        t1 = time.perf_counter()
        times_acquire.append(t1 - t0)
        assert acquired, f"锁获取应成功: {lock_key}"
        # 释放锁以便下一次测试
        await lock.release()

    stats_acquire = _compute_stats(times_acquire)
    _print_stats("DistributedLock.acquire", stats_acquire)

    # ---------- Benchmark: release ----------
    times_release: list[float] = []
    for i in range(ITERATIONS):
        lock_key = f"{lock_name}:release:{i}"
        lock = DistributedLock(
            redis_client,
            lock_key,
            ttl=10.0,
            retry_times=1,
            retry_delay=0.01,
        )
        await lock.acquire()
        t0 = time.perf_counter()
        released = await lock.release()
        t1 = time.perf_counter()
        times_release.append(t1 - t0)
        assert released, f"锁释放应成功: {lock_key}"

    stats_release = _compute_stats(times_release)
    _print_stats("DistributedLock.release", stats_release)

    # ---------- Benchmark: acquire + release (full cycle) ----------
    times_cycle: list[float] = []
    for i in range(ITERATIONS):
        lock_key = f"{lock_name}:cycle:{i}"
        lock = DistributedLock(
            redis_client,
            lock_key,
            ttl=10.0,
            retry_times=1,
            retry_delay=0.01,
        )
        t0 = time.perf_counter()
        await lock.acquire()
        await lock.release()
        t1 = time.perf_counter()
        times_cycle.append(t1 - t0)

    stats_cycle = _compute_stats(times_cycle)
    _print_stats("DistributedLock.acquire+release", stats_cycle)

    assert stats_acquire["avg_ms"] < 10, "锁获取平均耗时不应超过 10ms"
    assert stats_release["avg_ms"] < 10, "锁释放平均耗时不应超过 10ms"
    assert stats_cycle["avg_ms"] < 20, "锁完整周期平均耗时不应超过 20ms"
