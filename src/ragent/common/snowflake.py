"""
Snowflake 分布式 ID 生成器。

基于 Twitter Snowflake 算法，在分布式环境下生成全局唯一、趋势递增的 64 位整数 ID。

ID 结构（共 64 位）::

    0 | 00000000000000000000000000000000000000000 | 0000000000 | 000000000000
    符号位（1 位）        时间戳（41 位）          工作机器 ID（10 位）  序列号（12 位）

- 符号位：   1 位，始终为 0
- 时间戳：   41 位，毫秒精度，相对于自定义纪元（epoch），可用约 69 年
- 工作机器 ID：10 位，最大支持 1024 个工作节点
- 序列号：   12 位，同一毫秒内最大支持 4096 个 ID

通过 Redis Lua 脚本实现原子化的 worker_id 分配，保证各节点 ID 不冲突。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  位运算常量
# ---------------------------------------------------------------------------

#: 工作机器 ID 占用位数
WORKER_ID_BITS: int = 10

#: 序列号占用位数
SEQUENCE_BITS: int = 12

#: 工作机器 ID 最大值（0 ~ 1023）
MAX_WORKER_ID: int = (1 << WORKER_ID_BITS) - 1

#: 序列号最大值（0 ~ 4095）
MAX_SEQUENCE: int = (1 << SEQUENCE_BITS) - 1

#: 工作机器 ID 左移位数
WORKER_ID_SHIFT: int = SEQUENCE_BITS

#: 时间戳左移位数
TIMESTAMP_SHIFT: int = SEQUENCE_BITS + WORKER_ID_BITS


# ---------------------------------------------------------------------------
#  Redis Lua 脚本 —— 原子化 worker_id 分配
# ---------------------------------------------------------------------------

#: 通过 INCR 原子递增计数器，自动分配 worker_id。
#: 返回分配后的值（即当前节点使用的 worker_id）。
ALLOCATE_WORKER_ID: str = """
local current = redis.call('INCR', KEYS[1])
if current > tonumber(ARGV[1]) then
    redis.call('DECR', KEYS[1])
    return -1
end
return current
"""

#: Redis 中用于原子递增的键名
_WORKER_ID_COUNTER_KEY: str = "snowflake:worker_id_counter"


# ---------------------------------------------------------------------------
#  异常
# ---------------------------------------------------------------------------

class ClockBackwardError(Exception):
    """当时钟回拨时抛出的异常。"""

    def __init__(self, last_timestamp: int, current_timestamp: int) -> None:
        self.last_timestamp: int = last_timestamp
        self.current_timestamp: int = current_timestamp
        super().__init__(
            f"时钟回拨检测：上次时间戳 {last_timestamp}，当前时间戳 {current_timestamp}，"
            f"回拨了 {last_timestamp - current_timestamp} 毫秒"
        )


class WorkerIdExhaustedError(Exception):
    """当可用 worker_id 已耗尽时抛出的异常。"""

    def __init__(self, max_worker_id: int) -> None:
        self.max_worker_id: int = max_worker_id
        super().__init__(
            f"可用的 worker_id 已耗尽，最大值为 {max_worker_id}"
        )


class SequenceOverflowError(Exception):
    """同一毫秒内序列号溢出时抛出的异常（内部已自动处理，通常不会暴露给调用方）。"""

    def __init__(self, timestamp: int) -> None:
        self.timestamp: int = timestamp
        super().__init__(
            f"毫秒 {timestamp} 内序列号溢出，已超过最大值 {MAX_SEQUENCE}"
        )


# ---------------------------------------------------------------------------
#  worker_id 分配
# ---------------------------------------------------------------------------

async def allocate_worker_id(
    redis_client: Union[object, "redis.asyncio.Redis"],
) -> int:
    """通过 Redis 原子递增分配一个唯一的 worker_id。

    使用 Lua 脚本保证 INCR 操作与上限检查的原子性。
    如果已分配的 worker_id 数量超过最大值，将抛出
    :class:`WorkerIdExhaustedError`。

    Args:
        redis_client: 可执行 ``eval`` 的 Redis 客户端（支持 ``redis.asyncio.Redis``）。

    Returns:
        分配到的 worker_id（1 ~ MAX_WORKER_ID）。

    Raises:
        WorkerIdExhaustedError: 可用的 worker_id 已耗尽。
    """
    result = await redis_client.eval(
        ALLOCATE_WORKER_ID,
        1,
        _WORKER_ID_COUNTER_KEY,
        MAX_WORKER_ID,
    )
    worker_id: int = int(result)
    if worker_id < 0:
        raise WorkerIdExhaustedError(MAX_WORKER_ID)
    logger.info("通过 Redis 分配 worker_id: %d", worker_id)
    return worker_id


def allocate_worker_id_sync(
    redis_client: object,
) -> int:
    """同步版本的 worker_id 分配（用于同步 Redis 客户端）。

    Args:
        redis_client: 可执行 ``eval`` 的同步 Redis 客户端。

    Returns:
        分配到的 worker_id（1 ~ MAX_WORKER_ID）。

    Raises:
        WorkerIdExhaustedError: 可用的 worker_id 已耗尽。
    """
    result = redis_client.eval(
        ALLOCATE_WORKER_ID,
        1,
        _WORKER_ID_COUNTER_KEY,
        MAX_WORKER_ID,
    )
    worker_id: int = int(result)
    if worker_id < 0:
        raise WorkerIdExhaustedError(MAX_WORKER_ID)
    logger.info("通过 Redis 分配 worker_id: %d", worker_id)
    return worker_id


# ---------------------------------------------------------------------------
#  SnowflakeIdGenerator
# ---------------------------------------------------------------------------

class SnowflakeIdGenerator:
    """Snowflake 分布式 ID 生成器。

    在单节点内生成全局唯一、趋势递增的 64 位整数 ID。使用
    ``threading.Lock`` 保证线程安全。

    ID 组成::

        (timestamp - epoch) << TIMESTAMP_SHIFT
        | worker_id << WORKER_ID_SHIFT
        | sequence

    用法::

        gen = SnowflakeIdGenerator(worker_id=1)
        uid = gen.generate_id()

    Args:
        worker_id: 工作机器 ID，取值范围 0 ~ 1023。
        epoch: 起始纪元时间戳（毫秒），默认为 2024-01-01 00:00:00 UTC。

    Raises:
        ValueError: worker_id 超出有效范围。
    """

    #: 默认纪元：2024-01-01 00:00:00 UTC（毫秒时间戳）
    DEFAULT_EPOCH: int = 1704067200000

    def __init__(self, worker_id: int, epoch: int = DEFAULT_EPOCH) -> None:
        if worker_id < 0 or worker_id > MAX_WORKER_ID:
            raise ValueError(
                f"worker_id 必须在 0 ~ {MAX_WORKER_ID} 之间，当前值: {worker_id}"
            )
        self._worker_id: int = worker_id
        self._epoch: int = epoch

        # 上一次生成 ID 的时间戳（毫秒）
        self._last_timestamp: int = 0
        # 同一毫秒内的序列号
        self._sequence: int = 0

        # 线程锁，保证 generate_id 的线程安全
        self._lock: threading.Lock = threading.Lock()

        logger.info(
            "SnowflakeIdGenerator 初始化完成: worker_id=%d, epoch=%d",
            self._worker_id,
            self._epoch,
        )

    # ---- 内部辅助 -----------------------------------------------------------

    @staticmethod
    def _current_millis() -> int:
        """获取当前时间戳（毫秒）。

        Returns:
            当前时间距离 Unix 纪元的毫秒数。
        """
        return int(time.time() * 1000)

    def _wait_next_millis(self, last_timestamp: int) -> int:
        """自旋等待直到获得比 last_timestamp 更新的毫秒时间戳。

        当同一毫秒内序列号溢出时调用，确保下一个 ID 的时间戳前进 1 毫秒。

        Args:
            last_timestamp: 需要超过的上一毫秒时间戳。

        Returns:
            新的毫秒时间戳（严格大于 last_timestamp）。
        """
        timestamp = self._current_millis()
        while timestamp <= last_timestamp:
            time.sleep(0.0001)  # 短暂休眠，避免密集自旋占用 CPU
            timestamp = self._current_millis()
        return timestamp

    # ---- 公开接口 -----------------------------------------------------------

    @property
    def worker_id(self) -> int:
        """当前工作机器 ID。"""
        return self._worker_id

    @property
    def epoch(self) -> int:
        """起始纪元时间戳（毫秒）。"""
        return self._epoch

    def generate_id(self) -> int:
        """生成下一个全局唯一 ID（线程安全）。

        算法流程：
            1. 获取当前毫秒时间戳
            2. 与上一次时间戳比较：
               - 相同：递增序列号，若溢出则自旋等待下一毫秒
               - 较新：重置序列号为 0
               - 较旧（时钟回拨）：抛出 :class:`ClockBackbackError`
            3. 组装并返回 64 位 ID

        Returns:
            全局唯一的 64 位整数 ID。

        Raises:
            ClockBackwardError: 检测到系统时钟回拨。
        """
        with self._lock:
            timestamp = self._current_millis()

            if timestamp < self._last_timestamp:
                # 时钟回拨 —— 拒绝生成 ID
                raise ClockBackwardError(self._last_timestamp, timestamp)

            if timestamp == self._last_timestamp:
                # 同一毫秒内递增序列号
                self._sequence = (self._sequence + 1) & MAX_SEQUENCE
                if self._sequence == 0:
                    # 序列号溢出，自旋等待下一毫秒
                    timestamp = self._wait_next_millis(self._last_timestamp)
            else:
                # 新的毫秒，重置序列号
                self._sequence = 0

            self._last_timestamp = timestamp

            # 组装 ID
            snowflake_id: int = (
                ((timestamp - self._epoch) << TIMESTAMP_SHIFT)
                | (self._worker_id << WORKER_ID_SHIFT)
                | self._sequence
            )
            return snowflake_id

    def parse_id(self, snowflake_id: int) -> dict[str, int]:
        """解析 Snowflake ID，提取时间戳、worker_id 和序列号。

        Args:
            snowflake_id: 待解析的 Snowflake ID。

        Returns:
            包含 ``timestamp``（毫秒时间戳）、``worker_id``、``sequence`` 的字典。
        """
        sequence: int = snowflake_id & MAX_SEQUENCE
        worker_id: int = (snowflake_id >> WORKER_ID_SHIFT) & MAX_WORKER_ID
        timestamp_delta: int = snowflake_id >> TIMESTAMP_SHIFT
        return {
            "timestamp": timestamp_delta + self._epoch,
            "worker_id": worker_id,
            "sequence": sequence,
        }


# ---------------------------------------------------------------------------
#  模块级单例 & 便捷函数
# ---------------------------------------------------------------------------

_global_generator: Optional[SnowflakeIdGenerator] = None
_global_lock: threading.Lock = threading.Lock()


def get_id_generator(worker_id: int = 0, epoch: int = SnowflakeIdGenerator.DEFAULT_EPOCH) -> SnowflakeIdGenerator:
    """获取全局 Snowflake ID 生成器（惰性单例）。

    首次调用时创建实例，后续调用返回同一实例。

    Args:
        worker_id: 工作机器 ID（仅首次调用时有效），默认 0。
        epoch: 起始纪元时间戳（仅首次调用时有效），默认 2024-01-01 UTC。

    Returns:
        全局唯一的 :class:`SnowflakeIdGenerator` 实例。
    """
    global _global_generator
    if _global_generator is None:
        with _global_lock:
            # 双重检查锁定
            if _global_generator is None:
                _global_generator = SnowflakeIdGenerator(worker_id=worker_id, epoch=epoch)
    return _global_generator


def generate_id() -> int:
    """便捷函数：通过全局单例生成器生成下一个 ID。

    等价于 ``get_id_generator().generate_id()``。

    Returns:
        全局唯一的 64 位整数 ID。
    """
    return get_id_generator().generate_id()
