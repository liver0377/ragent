"""Tests for ragent.common.snowflake module."""
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragent.common.snowflake import (
    MAX_SEQUENCE,
    MAX_WORKER_ID,
    SEQUENCE_BITS,
    TIMESTAMP_SHIFT,
    WORKER_ID_BITS,
    WORKER_ID_SHIFT,
    ClockBackwardError,
    SequenceOverflowError,
    SnowflakeIdGenerator,
    WorkerIdExhaustedError,
    allocate_worker_id,
    allocate_worker_id_sync,
    generate_id,
    get_id_generator,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_worker_id_bits(self):
        assert WORKER_ID_BITS == 10

    def test_sequence_bits(self):
        assert SEQUENCE_BITS == 12

    def test_max_worker_id(self):
        assert MAX_WORKER_ID == (1 << WORKER_ID_BITS) - 1
        assert MAX_WORKER_ID == 1023

    def test_max_sequence(self):
        assert MAX_SEQUENCE == (1 << SEQUENCE_BITS) - 1
        assert MAX_SEQUENCE == 4095

    def test_shifts(self):
        assert WORKER_ID_SHIFT == SEQUENCE_BITS == 12
        assert TIMESTAMP_SHIFT == SEQUENCE_BITS + WORKER_ID_BITS == 22


# ---------------------------------------------------------------------------
# SnowflakeIdGenerator
# ---------------------------------------------------------------------------

class TestSnowflakeIdGenerator:
    def test_valid_worker_id(self):
        gen = SnowflakeIdGenerator(worker_id=0)
        assert gen.worker_id == 0

    def test_max_valid_worker_id(self):
        gen = SnowflakeIdGenerator(worker_id=MAX_WORKER_ID)
        assert gen.worker_id == MAX_WORKER_ID

    def test_negative_worker_id_raises(self):
        with pytest.raises(ValueError, match="worker_id"):
            SnowflakeIdGenerator(worker_id=-1)

    def test_too_large_worker_id_raises(self):
        with pytest.raises(ValueError, match="worker_id"):
            SnowflakeIdGenerator(worker_id=MAX_WORKER_ID + 1)

    def test_default_epoch(self):
        gen = SnowflakeIdGenerator(worker_id=1)
        assert gen.epoch == SnowflakeIdGenerator.DEFAULT_EPOCH
        assert gen.epoch == 1704067200000

    def test_custom_epoch(self):
        gen = SnowflakeIdGenerator(worker_id=1, epoch=1000)
        assert gen.epoch == 1000

    def test_generate_id_positive(self):
        gen = SnowflakeIdGenerator(worker_id=1)
        sid = gen.generate_id()
        assert sid > 0

    def test_generate_ids_unique(self):
        gen = SnowflakeIdGenerator(worker_id=1)
        ids = set()
        for _ in range(5000):
            ids.add(gen.generate_id())
        # All should be unique
        assert len(ids) == 5000

    def test_generate_ids_monotonic(self):
        gen = SnowflakeIdGenerator(worker_id=1)
        prev = gen.generate_id()
        for _ in range(100):
            curr = gen.generate_id()
            assert curr >= prev
            prev = curr

    def test_parse_id_roundtrip(self):
        gen = SnowflakeIdGenerator(worker_id=42)
        sid = gen.generate_id()
        parsed = gen.parse_id(sid)
        assert parsed["worker_id"] == 42
        assert parsed["sequence"] >= 0
        assert parsed["timestamp"] > 0

    def test_parse_id_multiple(self):
        gen = SnowflakeIdGenerator(worker_id=100)
        for _ in range(50):
            sid = gen.generate_id()
            parsed = gen.parse_id(sid)
            assert parsed["worker_id"] == 100

    def test_clock_backward_detection(self):
        gen = SnowflakeIdGenerator(worker_id=1)
        # Force a large last_timestamp into the future
        gen._last_timestamp = SnowflakeIdGenerator._current_millis() + 10000
        with pytest.raises(ClockBackwardError):
            gen.generate_id()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_generate_ids(self):
        gen = SnowflakeIdGenerator(worker_id=1)
        ids = []
        errors = []

        def generate_many(n):
            try:
                for _ in range(n):
                    ids.append(gen.generate_id())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=generate_many, args=(200,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(ids) == 2000
        assert len(set(ids)) == 2000  # all unique


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class TestModuleHelpers:
    def test_get_id_generator_returns_same_instance(self):
        g1 = get_id_generator()
        g2 = get_id_generator()
        assert g1 is g2

    def test_generate_id_from_module(self):
        sid = generate_id()
        assert isinstance(sid, int)
        assert sid > 0


# ---------------------------------------------------------------------------
# allocate_worker_id (async)
# ---------------------------------------------------------------------------

class TestAllocateWorkerId:
    @pytest.mark.asyncio
    async def test_allocate_worker_id_success(self):
        mock_redis = AsyncMock()
        mock_redis.eval.return_value = 5
        wid = await allocate_worker_id(mock_redis)
        assert wid == 5

    @pytest.mark.asyncio
    async def test_allocate_worker_id_exhausted(self):
        mock_redis = AsyncMock()
        mock_redis.eval.return_value = -1
        with pytest.raises(WorkerIdExhaustedError):
            await allocate_worker_id(mock_redis)


class TestAllocateWorkerIdSync:
    def test_allocate_worker_id_sync_success(self):
        mock_redis = MagicMock()
        mock_redis.eval.return_value = 3
        wid = allocate_worker_id_sync(mock_redis)
        assert wid == 3

    def test_allocate_worker_id_sync_exhausted(self):
        mock_redis = MagicMock()
        mock_redis.eval.return_value = -1
        with pytest.raises(WorkerIdExhaustedError):
            allocate_worker_id_sync(mock_redis)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class TestCustomExceptions:
    def test_clock_backward_error(self):
        err = ClockBackwardError(1000, 900)
        assert err.last_timestamp == 1000
        assert err.current_timestamp == 900
        assert "1000" in str(err)

    def test_worker_id_exhausted_error(self):
        err = WorkerIdExhaustedError(1023)
        assert err.max_worker_id == 1023
        assert "1023" in str(err)

    def test_sequence_overflow_error(self):
        err = SequenceOverflowError(9999)
        assert err.timestamp == 9999
        assert "9999" in str(err)
