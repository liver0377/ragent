"""
分布式限流排队器 —— 基于 Redis ZSET 排队 + asyncio.Semaphore 并发控制

提供以下功能：
    - ``RateLimitResult`` —— 限流结果数据类
    - ``RateLimiter``     —— 分布式限流器

工作流程：
    1. 请求入队（Redis ZSET，按时间戳排序）
    2. Lua 脚本原子判断是否在窗口内
    3. 在窗口内 → 获取 Semaphore 许可 → 执行
    4. 不在窗口内 → 等待通知（Redis Pub/Sub）→ 重试
    5. 完成后 → 释放 Semaphore + ZREM + 通知下一个

设计要点：
    - 每个用户独立的 ZSET 队列，实现用户级公平调度
    - Lua 脚本保证排队位置判断的原子性
    - asyncio.Semaphore 控制本地并发上限
    - SSE 事件流实时推送排队进度
    - ZSET 设置 TTL 自动清理过期请求
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from ragent.common.exceptions import ClientException, ServiceException
from ragent.common.logging import get_logger
from ragent.common.sse import SSEEvent, SSEEventType

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
#  Lua 脚本 —— 原子位置检查
# ---------------------------------------------------------------------------

_CHECK_POSITION_SCRIPT = """
local key = KEYS[1]
local member = ARGV[1]
local max_concurrent = tonumber(ARGV[2])
local rank = redis.call('ZRANK', key, member)
if rank == false then
    return -1
end
if rank < max_concurrent then
    return 0
else
    return rank
end
"""


# ---------------------------------------------------------------------------
#  RateLimitResult —— 限流结果数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitResult:
    """限流排队结果。

    Attributes:
        position:   排队位置。0 表示已获得许可可立即执行，>0 表示在队列中等待。
        request_id: 请求唯一标识（UUID），用于后续释放和查询。
        is_ready:   是否已就绪可执行（等价于 ``position == 0``）。
    """

    position: int
    request_id: str
    is_ready: bool

    def __post_init__(self) -> None:
        """校验字段一致性。"""
        # frozen=True 下需要通过 object.__setattr__ 修改
        object.__setattr__(self, "is_ready", self.position == 0)


# ---------------------------------------------------------------------------
#  RateLimiter —— 分布式限流排队器
# ---------------------------------------------------------------------------


class RateLimiter:
    """分布式限流排队器 —— 基于 Redis ZSET 排队 + asyncio.Semaphore 并发控制。

    工作流程：
        1. 请求入队（Redis ZSET，按时间戳排序）
        2. Lua 脚本原子判断是否在窗口内
        3. 在窗口内 → 获取 Semaphore 许可 → 执行
        4. 不在窗口内 → 等待通知（轮询）→ 重试
        5. 完成后 → 释放 Semaphore + ZREM + 通知下一个

    用法::

        limiter = RateLimiter(max_concurrent=5)

        # 获取许可
        result = await limiter.acquire(user_id=42)
        if result.is_ready:
            try:
                await do_work()
            finally:
                await limiter.release(user_id=42, request_id=result.request_id)
        else:
            # 排队等待
            ...

        # 或者通过 wait_for_turn 等待轮次（SSE 流式推送）
        async for event in limiter.wait_for_turn(user_id=42):
            print(event)
    """

    def __init__(
        self,
        max_concurrent: int = 5,
        queue_key_prefix: str = "ragent:queue",
        notify_channel_prefix: str = "ragent:notify",
        semaphore_timeout: float = 300.0,
        queue_ttl: int = 600,
        poll_interval: float = 1.0,
    ) -> None:
        """初始化限流器。

        Args:
            max_concurrent:        最大并发数，同时执行的请求数上限。
            queue_key_prefix:      Redis ZSET 键前缀，最终键格式为 ``{prefix}:{user_id}``。
            notify_channel_prefix: Redis Pub/Sub 通知频道前缀。
            semaphore_timeout:     获取 Semaphore 的超时时间（秒）。
            queue_ttl:             ZSET 键的过期时间（秒），用于自动清理残留请求。
            poll_interval:         排队轮询间隔（秒）。
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent 必须大于 0，当前值: {max_concurrent}")
        if semaphore_timeout <= 0:
            raise ValueError(f"semaphore_timeout 必须大于 0，当前值: {semaphore_timeout}")
        if poll_interval <= 0:
            raise ValueError(f"poll_interval 必须大于 0，当前值: {poll_interval}")

        self._max_concurrent: int = max_concurrent
        self._queue_key_prefix: str = queue_key_prefix
        self._notify_channel_prefix: str = notify_channel_prefix
        self._semaphore_timeout: float = semaphore_timeout
        self._queue_ttl: int = queue_ttl
        self._poll_interval: float = poll_interval

        # 本地信号量，控制实际并发数
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)

        # 缓存 Lua 脚本的 SHA1（首次调用后填充，用于 EVALSHA 优化）
        self._lua_sha: str | None = None

    # ---- 内部辅助 ----------------------------------------------------------

    def _queue_key(self, user_id: int | str) -> str:
        """构造用户队列的 Redis 键名。

        Args:
            user_id: 用户ID。

        Returns:
            格式为 ``{queue_key_prefix}:{user_id}`` 的字符串。
        """
        return f"{self._queue_key_prefix}:{user_id}"

    def _notify_channel(self, user_id: int | str) -> str:
        """构造用户通知频道的名称。

        Args:
            user_id: 用户ID。

        Returns:
            格式为 ``{notify_channel_prefix}:{user_id}`` 的字符串。
        """
        return f"{self._notify_channel_prefix}:{user_id}"

    def _get_redis(self) -> Any:
        """获取 Redis 客户端实例。

        延迟导入以避免循环依赖，同时允许在未初始化 Redis 的环境下进行测试。

        Returns:
            redis.asyncio.Redis 实例。

        Raises:
            ServiceException: Redis 未初始化时抛出。
        """
        try:
            from ragent.common.redis_manager import get_redis_manager

            manager = get_redis_manager()
            return manager.get_redis()
        except RuntimeError as exc:
            raise ServiceException(
                error_code="B2101",
                message=f"Redis 未初始化，无法使用限流器: {exc}",
            ) from exc

    # ---- 核心操作 -----------------------------------------------------------

    async def acquire(self, user_id: int | str) -> RateLimitResult:
        """获取执行许可，将请求加入排队队列。

        将请求以当前时间戳作为分数加入用户专属的 Redis ZSET，
        然后通过 Lua 脚本原子判断排队位置。

        Args:
            user_id: 用户ID。

        Returns:
            RateLimitResult 包含排队位置、请求ID和就绪状态。
            position=0 表示已就绪，>0 表示排队中。
        """
        request_id: str = uuid.uuid4().hex
        queue_key: str = self._queue_key(user_id)
        now: float = time.time()

        redis = self._get_redis()

        # 1. 加入 ZSET（按时间戳排序）
        await redis.zadd(queue_key, {request_id: now})

        # 2. 设置 TTL 防止残留
        await redis.expire(queue_key, self._queue_ttl)

        logger.debug(
            "请求入队 | user_id=%s | request_id=%s | queue_key=%s",
            user_id,
            request_id,
            queue_key,
        )

        # 3. 原子检查排队位置
        position = await self._check_position(redis, queue_key, request_id)

        result = RateLimitResult(position=position, request_id=request_id, is_ready=(position == 0))

        if result.is_ready:
            logger.info(
                "请求已就绪 | user_id=%s | request_id=%s",
                user_id,
                request_id,
            )
        else:
            logger.info(
                "请求排队中 | user_id=%s | request_id=%s | position=%d",
                user_id,
                request_id,
                position,
            )

        return result

    async def release(self, user_id: int | str, request_id: str) -> None:
        """释放许可，从队列中移除请求并通知下一个等待者。

        执行以下步骤：
            1. 从 Redis ZSET 中移除请求
            2. 释放本地 Semaphore
            3. 通过 Redis Pub/Sub 通知下一个等待者

        Args:
            user_id:    用户ID。
            request_id: 请求唯一ID（由 acquire 返回）。
        """
        queue_key: str = self._queue_key(user_id)
        notify_channel: str = self._notify_channel(user_id)

        redis = self._get_redis()

        # 1. 从 ZSET 移除
        removed = await redis.zrem(queue_key, request_id)
        if removed:
            logger.debug(
                "请求已从队列移除 | user_id=%s | request_id=%s",
                user_id,
                request_id,
            )
        else:
            logger.warning(
                "请求不在队列中（可能已过期或已释放） | user_id=%s | request_id=%s",
                user_id,
                request_id,
            )

        # 2. 释放 Semaphore
        try:
            self._semaphore.release()
        except ValueError:
            # Semaphore 计数已满，忽略（可能存在重复释放）
            logger.warning(
                "Semaphore 释放失败（计数已满） | user_id=%s | request_id=%s",
                user_id,
                request_id,
            )

        # 3. 通知下一个等待者
        try:
            await redis.publish(notify_channel, "release")
        except Exception:
            # Pub/Sub 失败不应阻塞主流程
            logger.warning(
                "通知发布失败 | channel=%s | user_id=%s",
                notify_channel,
                user_id,
            )

        logger.info(
            "许可已释放 | user_id=%s | request_id=%s",
            user_id,
            request_id,
        )

    async def get_queue_position(self, user_id: int | str, request_id: str) -> int:
        """查询当前排队位置。

        Args:
            user_id:    用户ID。
            request_id: 请求唯一ID。

        Returns:
            0 表示正在执行（或在并发窗口内），>0 表示在队列中的位置，
            -1 表示请求不存在（可能已过期或已完成）。
        """
        queue_key: str = self._queue_key(user_id)
        redis = self._get_redis()

        return await self._check_position(redis, queue_key, request_id)

    async def wait_for_turn(
        self,
        user_id: int | str,
        timeout: float = 300.0,
    ) -> AsyncIterator[SSEEvent]:
        """等待轮次，通过 SSE 事件推送排队状态。

        该方法是一个异步生成器，持续轮询排队位置并通过 SSE 事件
        推送当前状态。当获取到执行许可后，推送就绪事件并返回。

        Yields:
            SSEEvent 事件对象：
            - event="queue_status", data={"position": N, "status": "waiting"}
              表示排队中，N 为当前排队位置
            - event="queue_status", data={"position": 0, "status": "processing"}
              表示已获得许可，可以开始执行

        Args:
            user_id: 用户ID。
            timeout: 等待超时时间（秒），默认 300 秒。

        Raises:
            ClientException: 等待超时。
            ServiceException: 获取 Semaphore 超时。
        """
        if timeout <= 0:
            raise ValueError(f"timeout 必须大于 0，当前值: {timeout}")

        # 1. 首先入队
        result: RateLimitResult = await self.acquire(user_id)
        request_id: str = result.request_id

        if result.is_ready:
            # 立即尝试获取 Semaphore
            acquired = await self._try_acquire_semaphore(request_id)
            if acquired:
                yield SSEEvent(
                    event="queue_status",
                    data=json.dumps(
                        {"position": 0, "status": "processing", "request_id": request_id},
                        ensure_ascii=False,
                    ),
                )
                return
            # Semaphore 获取失败，回退到排队等待

        # 2. 排队等待循环
        start_time: float = time.monotonic()
        try:
            while True:
                elapsed: float = time.monotonic() - start_time
                if elapsed >= timeout:
                    # 超时，清理并抛出异常
                    await self._cleanup_on_timeout(user_id, request_id)
                    raise ClientException(
                        error_code="A1010",
                        message=f"排队等待超时（{timeout}秒），请稍后重试",
                    )

                # 查询当前排队位置
                position: int = await self.get_queue_position(user_id, request_id)

                if position == -1:
                    # 请求不在队列中（可能被清理了），重新入队
                    logger.warning(
                        "排队请求丢失，重新入队 | user_id=%s | request_id=%s",
                        user_id,
                        request_id,
                    )
                    result = await self.acquire(user_id)
                    request_id = result.request_id
                    position = result.position

                if position == 0:
                    # 在窗口内，尝试获取 Semaphore
                    acquired = await self._try_acquire_semaphore(request_id)
                    if acquired:
                        yield SSEEvent(
                            event="queue_status",
                            data=json.dumps(
                                {
                                    "position": 0,
                                    "status": "processing",
                                    "request_id": request_id,
                                },
                                ensure_ascii=False,
                            ),
                        )
                        return
                    # Semaphore 暂时无法获取，继续等待
                    yield SSEEvent(
                        event="queue_status",
                        data=json.dumps(
                            {"position": 0, "status": "waiting_semaphore"},
                            ensure_ascii=False,
                        ),
                    )
                else:
                    # 排队中
                    yield SSEEvent(
                        event="queue_status",
                        data=json.dumps(
                            {"position": position, "status": "waiting"},
                            ensure_ascii=False,
                        ),
                    )

                # 等待轮询间隔或被通知唤醒
                remaining: float = timeout - (time.monotonic() - start_time)
                if remaining <= 0:
                    await self._cleanup_on_timeout(user_id, request_id)
                    raise ClientException(
                        error_code="A1010",
                        message=f"排队等待超时（{timeout}秒），请稍后重试",
                    )

                wait_time: float = min(self._poll_interval, remaining)
                await asyncio.sleep(wait_time)

        except (ClientException, ServiceException):
            raise
        except Exception as exc:
            logger.exception(
                "等待轮次异常 | user_id=%s | request_id=%s", user_id, request_id
            )
            await self._cleanup_on_timeout(user_id, request_id)
            raise ServiceException(
                error_code="B2102",
                message=f"排队等待异常: {exc}",
            ) from exc

    # ---- 上下文管理器封装 ----------------------------------------------------

    async def execute(
        self,
        user_id: int | str,
        timeout: float = 300.0,
    ) -> AsyncIterator[SSEEvent]:
        """获取执行许可并通过 SSE 推送排队进度。

        这是 ``wait_for_turn`` 的别名，提供更直观的语义。

        Args:
            user_id: 用户ID。
            timeout: 等待超时时间（秒）。

        Yields:
            SSEEvent 排队状态事件。
        """
        async for event in self.wait_for_turn(user_id, timeout=timeout):
            yield event

    # ---- 内部方法 -----------------------------------------------------------

    async def _check_position(
        self,
        redis: Any,
        queue_key: str,
        request_id: str,
    ) -> int:
        """通过 Lua 脚本原子检查请求在队列中的位置。

        Args:
            redis:      Redis 客户端。
            queue_key:  队列键名。
            request_id: 请求ID。

        Returns:
            0 表示就绪（在并发窗口内），>0 表示排队位置，-1 表示不存在。
        """
        # 尝试使用 EVALSHA（性能优化）
        try:
            if self._lua_sha is not None:
                result = await redis.evalsha(
                    self._lua_sha, 1, queue_key, request_id, str(self._max_concurrent)
                )
                return int(result)
        except Exception:
            # EVALSHA 失败（脚本不在缓存中），回退到 EVAL
            self._lua_sha = None

        # 使用 EVAL 执行脚本并缓存 SHA
        result = await redis.eval(
            _CHECK_POSITION_SCRIPT, 1, queue_key, request_id, str(self._max_concurrent)
        )

        # 缓存 Lua 脚本 SHA 以便后续 EVALSHA 调用
        # redis-py 的 eval 返回结果后，脚本已被加载到 Redis
        try:
            import hashlib

            self._lua_sha = hashlib.sha1(_CHECK_POSITION_SCRIPT.encode()).hexdigest()
        except Exception:
            pass

        return int(result)

    async def _try_acquire_semaphore(self, request_id: str) -> bool:
        """尝试在超时内获取 Semaphore 许可。

        Args:
            request_id: 请求ID（用于日志）。

        Returns:
            True 表示成功获取，False 表示超时。
        """
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self._semaphore_timeout,
            )
            logger.debug("Semaphore 许可获取成功 | request_id=%s", request_id)
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "Semaphore 获取超时 | request_id=%s | timeout=%.1fs",
                request_id,
                self._semaphore_timeout,
            )
            return False

    async def _cleanup_on_timeout(
        self, user_id: int | str, request_id: str
    ) -> None:
        """超时时清理队列中的请求。

        Args:
            user_id:    用户ID。
            request_id: 请求ID。
        """
        try:
            queue_key: str = self._queue_key(user_id)
            redis = self._get_redis()
            await redis.zrem(queue_key, request_id)
            logger.info(
                "超时清理完成 | user_id=%s | request_id=%s",
                user_id,
                request_id,
            )
        except Exception:
            logger.exception(
                "超时清理失败 | user_id=%s | request_id=%s",
                user_id,
                request_id,
            )
