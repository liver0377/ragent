"""
异步 Redis 连接池管理器，支持分布式锁。

提供基于 redis-py async 的连接池管理、常用数据结构操作以及分布式锁实现。
使用单例模式确保全局共享同一连接池实例。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional, Union

import redis.asyncio
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  DistributedLock — 分布式锁
# ---------------------------------------------------------------------------

class DistributedLock:
    """基于 Redis 的分布式锁（异步上下文管理器）。

    使用 SET key value NX PX ttl 原子命令获取锁，
    释放时通过 Lua 脚本校验 value 后再删除，防止误删其他持有者的锁。

    用法::

        async with redis_manager.lock("my-lock", ttl=30):
            # 在锁保护下执行临界区逻辑
            ...
    """

    # Lua 释放脚本：仅当锁的值与当前持有者匹配时才删除
    _RELEASE_SCRIPT = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    else
        return 0
    end
    """

    # Lua 续期脚本：仅当锁的值与当前持有者匹配时才重设过期时间
    _EXTEND_SCRIPT = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("PEXPIRE", KEYS[1], ARGV[2])
    else
        return 0
    end
    """

    def __init__(
        self,
        redis_client: Redis,
        name: str,
        *,
        ttl: float = 30.0,
        retry_times: int = 3,
        retry_delay: float = 0.2,
        blocking_timeout: Optional[float] = None,
    ) -> None:
        """初始化分布式锁。

        Args:
            redis_client: Redis 异步客户端实例。
            name: 锁名称（不含前缀，由 RedisManager 添加命名空间）。
            ttl: 锁的超时时间（秒），到期后自动释放。
            retry_times: 获取锁失败后的最大重试次数。
            retry_delay: 重试之间的基础等待时间（秒），实际等待会指数退避。
            blocking_timeout: 最大等待时间（秒），超时后放弃获取锁。None 表示一直等待。
        """
        self._redis = redis_client
        self._name = name
        self._ttl_ms = int(ttl * 1000)
        self._retry_times = retry_times
        self._retry_delay = retry_delay
        self._blocking_timeout = blocking_timeout
        # 每个锁实例生成唯一标识，用于安全释放
        self._token = uuid.uuid4().hex

    @property
    def name(self) -> str:
        """锁名称。"""
        return self._name

    # ---- 获取 / 释放 -------------------------------------------------------

    async def acquire(self) -> bool:
        """尝试获取分布式锁。

        使用 SET key value NX PX ttl 原子命令保证互斥性。
        失败时按指数退避策略重试，直到达到重试上限或阻塞超时。

        Returns:
            True 表示成功获取锁，False 表示获取失败。
        """
        attempt = 0
        total_waited = 0.0

        while True:
            acquired = await self._redis.set(
                self._name, self._token, nx=True, px=self._ttl_ms
            )
            if acquired:
                logger.debug("成功获取分布式锁: name=%s, token=%s", self._name, self._token)
                return True

            attempt += 1
            # 超出重试次数
            if attempt > self._retry_times:
                logger.warning(
                    "获取分布式锁失败（已达最大重试次数）: name=%s", self._name
                )
                return False

            # 检查阻塞超时
            delay = self._retry_delay * (2 ** (attempt - 1))
            if self._blocking_timeout is not None:
                if total_waited + delay >= self._blocking_timeout:
                    logger.warning(
                        "获取分布式锁超时: name=%s, waited=%.2fs", self._name, total_waited
                    )
                    return False
                delay = min(delay, self._blocking_timeout - total_waited)

            await asyncio.sleep(delay)
            total_waited += delay

    async def release(self) -> bool:
        """释放分布式锁。

        通过 Lua 脚本原子地检查 value 并删除，避免误删其他客户端持有的锁。

        Returns:
            True 表示成功释放，False 表示锁已不属于当前持有者。
        """
        result = await self._redis.eval(
            self._RELEASE_SCRIPT, 1, self._name, self._token
        )
        if result:
            logger.debug("成功释放分布式锁: name=%s, token=%s", self._name, self._token)
        else:
            logger.warning(
                "释放分布式锁失败（锁已不属于当前持有者）: name=%s, token=%s",
                self._name,
                self._token,
            )
        return bool(result)

    async def extend(self, additional_ttl: float) -> bool:
        """续期分布式锁。

        通过 Lua 脚本原子地检查 value 并重设过期时间。

        Args:
            additional_ttl: 额外延长的时间（秒）。

        Returns:
            True 表示续期成功，False 表示锁已不属于当前持有者。
        """
        result = await self._redis.eval(
            self._EXTEND_SCRIPT, 1, self._name, self._token, int(additional_ttl * 1000)
        )
        return bool(result)

    # ---- 异步上下文管理器 ---------------------------------------------------

    async def __aenter__(self) -> "DistributedLock":
        acquired = await self.acquire()
        if not acquired:
            raise RedisError(f"无法获取分布式锁: {self._name}")
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        await self.release()


# ---------------------------------------------------------------------------
#  RedisManager — Redis 连接池管理器
# ---------------------------------------------------------------------------

class RedisManager:
    """异步 Redis 连接池管理器（单例模式）。

    通过统一的命名空间前缀对键进行隔离，并提供常用数据结构操作
    以及分布式锁支持。

    用法::

        manager = get_redis_manager()
        await manager.init()
        try:
            await manager.set("user:1", "alice")
            value = await manager.get("user:1")
        finally:
            await manager.close()
    """

    _instance: Optional["RedisManager"] = None

    def __init__(self) -> None:
        """初始化管理器（不应直接调用，请使用 get_redis_manager()）。"""
        self._pool: Optional[ConnectionPool] = None
        self._redis: Optional[Redis] = None
        self._prefix: str = "ragent"

    # ---- 单例 ---------------------------------------------------------------

    @classmethod
    def _get_instance(cls) -> "RedisManager":
        """获取或创建单例实例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- 键命名空间 ---------------------------------------------------------

    def _key(self, name: str) -> str:
        """构造带命名空间前缀的完整键名。

        格式: {prefix}:{name}

        Args:
            name: 原始键名。

        Returns:
            带前缀的完整键名。
        """
        return f"{self._prefix}:{name}"

    # ---- 生命周期 -----------------------------------------------------------

    async def init(self) -> None:
        """创建连接池并初始化 Redis 客户端。

        从 ragent.config.settings 中读取 REDIS_URL 构建连接池。
        """
        from ragent.config.settings import get_settings

        settings = get_settings()
        redis_url: str = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        self._prefix = getattr(settings, "REDIS_KEY_PREFIX", "ragent")

        logger.info("正在初始化 Redis 连接池: url=%s, prefix=%s", redis_url, self._prefix)

        self._pool = ConnectionPool.from_url(
            redis_url,
            decode_responses=True,
            max_connections=20,
        )
        self._redis = Redis(connection_pool=self._pool)

        # 验证连接是否正常
        await self.ping()
        logger.info("Redis 连接池初始化完成")

    async def close(self) -> None:
        """关闭连接池，释放所有连接。"""
        if self._pool is not None:
            logger.info("正在关闭 Redis 连接池")
            await self._pool.aclose()
            self._pool = None
            self._redis = None

    def get_redis(self) -> Redis:
        """获取 Redis 异步客户端实例。

        Returns:
            Redis 客户端。

        Raises:
            RuntimeError: 连接池尚未初始化。
        """
        if self._redis is None:
            raise RuntimeError(
                "Redis 连接池尚未初始化，请先调用 init()"
            )
        return self._redis

    # ---- 健康检查 -----------------------------------------------------------

    async def ping(self) -> bool:
        """检查 Redis 连接是否正常。

        Returns:
            True 表示连接正常。
        """
        try:
            client = self.get_redis()
            result = await client.ping()
            return bool(result)
        except RedisError:
            logger.exception("Redis ping 失败")
            return False

    # ---- 键过期 / 存在 ------------------------------------------------------

    async def exists(self, name: str) -> bool:
        """判断键是否存在。

        Args:
            name: 键名（不含前缀）。

        Returns:
            键存在返回 True。
        """
        client = self.get_redis()
        return bool(await client.exists(self._key(name)))

    async def expire(self, name: str, seconds: int) -> bool:
        """设置键的过期时间。

        Args:
            name: 键名（不含前缀）。
            seconds: 过期时间（秒）。

        Returns:
            设置成功返回 True。
        """
        client = self.get_redis()
        return bool(await client.expire(self._key(name), seconds))

    async def ttl(self, name: str) -> int:
        """获取键的剩余过期时间。

        Args:
            name: 键名（不含前缀）。

        Returns:
            剩余秒数，-1 表示永不过期，-2 表示键不存在。
        """
        client = self.get_redis()
        return int(await client.ttl(self._key(name)))

    # ---- String 操作 --------------------------------------------------------

    async def get(self, name: str) -> Optional[str]:
        """获取字符串值。

        Args:
            name: 键名（不含前缀）。

        Returns:
            键对应的值，不存在返回 None。
        """
        client = self.get_redis()
        return await client.get(self._key(name))

    async def set(
        self,
        name: str,
        value: Union[str, int, float],
        *,
        ex: Optional[int] = None,
        px: Optional[int] = None,
        nx: bool = False,
        xx: bool = False,
    ) -> Optional[bool]:
        """设置字符串值。

        Args:
            name: 键名（不含前缀）。
            value: 要设置的值。
            ex: 过期时间（秒）。
            px: 过期时间（毫秒）。
            nx: 仅当键不存在时设置。
            xx: 仅当键已存在时设置。

        Returns:
            操作结果。
        """
        client = self.get_redis()
        return await client.set(
            self._key(name), value, ex=ex, px=px, nx=nx, xx=xx
        )

    async def delete(self, *names: str) -> int:
        """删除一个或多个键。

        Args:
            names: 键名列表（不含前缀）。

        Returns:
            被删除的键数量。
        """
        if not names:
            return 0
        client = self.get_redis()
        keys = [self._key(n) for n in names]
        return int(await client.delete(*keys))

    # ---- Hash 操作 ----------------------------------------------------------

    async def hset(
        self,
        name: str,
        key: str,
        value: Union[str, int, float],
    ) -> int:
        """设置哈希字段值。

        Args:
            name: 哈希键名（不含前缀）。
            key: 字段名。
            value: 字段值。

        Returns:
            新增字段数量。
        """
        client = self.get_redis()
        return int(await client.hset(self._key(name), key, value))

    async def hget(self, name: str, key: str) -> Optional[str]:
        """获取哈希字段值。

        Args:
            name: 哈希键名（不含前缀）。
            key: 字段名。

        Returns:
            字段值，不存在返回 None。
        """
        client = self.get_redis()
        return await client.hget(self._key(name), key)

    async def hgetall(self, name: str) -> dict[str, str]:
        """获取哈希的所有字段和值。

        Args:
            name: 哈希键名（不含前缀）。

        Returns:
            包含所有字段和值的字典。
        """
        client = self.get_redis()
        return await client.hgetall(self._key(name))

    async def hdel(self, name: str, *keys: str) -> int:
        """删除哈希中的一个或多个字段。

        Args:
            name: 哈希键名（不含前缀）。
            keys: 要删除的字段名列表。

        Returns:
            被删除的字段数量。
        """
        if not keys:
            return 0
        client = self.get_redis()
        return int(await client.hdel(self._key(name), *keys))

    # ---- Sorted Set 操作 ----------------------------------------------------

    async def zadd(
        self,
        name: str,
        mapping: dict[str, Union[int, float]],
    ) -> int:
        """向有序集合添加成员及其分数。

        Args:
            name: 有序集合键名（不含前缀）。
            mapping: 成员到分数的映射。

        Returns:
            新增成员数量。
        """
        client = self.get_redis()
        return int(await client.zadd(self._key(name), mapping))

    async def zrange(
        self,
        name: str,
        start: int,
        end: int,
        *,
        withscores: bool = False,
    ) -> list[Any]:
        """获取有序集合指定范围的成员。

        Args:
            name: 有序集合键名（不含前缀）。
            start: 起始索引。
            end: 结束索引。
            withscores: 是否同时返回分数。

        Returns:
            成员列表（可选带分数）。
        """
        client = self.get_redis()
        return await client.zrange(self._key(name), start, end, withscores=withscores)

    async def zremrangebyscore(
        self,
        name: str,
        min_score: Union[int, float, str],
        max_score: Union[int, float, str],
    ) -> int:
        """按分数范围移除有序集合中的成员。

        Args:
            name: 有序集合键名（不含前缀）。
            min_score: 最小分数（含），使用 "-inf" 表示无下界。
            max_score: 最大分数（含），使用 "+inf" 表示无上界。

        Returns:
            被移除的成员数量。
        """
        client = self.get_redis()
        return int(await client.zremrangebyscore(self._key(name), min_score, max_score))

    # ---- 分布式锁 -----------------------------------------------------------

    def lock(
        self,
        name: str,
        *,
        ttl: float = 30.0,
        retry_times: int = 3,
        retry_delay: float = 0.2,
        blocking_timeout: Optional[float] = None,
    ) -> DistributedLock:
        """创建分布式锁实例。

        锁键名会自动添加命名空间前缀。

        Args:
            name: 锁名称（不含前缀）。
            ttl: 锁的超时时间（秒）。
            retry_times: 获取锁失败后的最大重试次数。
            retry_delay: 重试之间的基础等待时间（秒）。
            blocking_timeout: 最大等待时间（秒），None 表示一直等待。

        Returns:
            DistributedLock 实例，可作为异步上下文管理器使用。

        用法::

            async with redis_manager.lock("my-lock", ttl=30):
                # 临界区
                ...
        """
        client = self.get_redis()
        lock_key = self._key(f"lock:{name}")
        return DistributedLock(
            client,
            lock_key,
            ttl=ttl,
            retry_times=retry_times,
            retry_delay=retry_delay,
            blocking_timeout=blocking_timeout,
        )


# ---------------------------------------------------------------------------
#  模块级便捷函数
# ---------------------------------------------------------------------------

def get_redis_manager() -> RedisManager:
    """获取 RedisManager 单例实例。

    首次调用时创建实例，后续调用返回同一实例。
    使用前需先调用 ``await manager.init()`` 初始化连接池。

    Returns:
        RedisManager 单例。
    """
    return RedisManager._get_instance()
