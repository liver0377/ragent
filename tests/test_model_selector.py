"""模型选择器模块的单元测试。"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from ragent.common.exceptions import RemoteException
from ragent.infra.ai.models import (
    CircuitBreakerConfig,
    ModelCandidate,
    ModelConfig,
    ModelConfigManager,
)
from ragent.infra.ai.model_selector import (
    BreakerState,
    ModelCircuitBreaker,
    ModelSelector,
)


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_manager(
    chat: list[ModelCandidate] | None = None,
    embedding: list[ModelCandidate] | None = None,
    cb_config: CircuitBreakerConfig | None = None,
) -> ModelConfigManager:
    """构建用于测试的 ModelConfigManager。"""
    config = ModelConfig(
        chat_models=chat or [],
        embedding_models=embedding or [],
        circuit_breaker=cb_config or CircuitBreakerConfig(),
    )
    return ModelConfigManager(config=config)


# ---------------------------------------------------------------------------
# ModelCircuitBreaker 测试
# ---------------------------------------------------------------------------

class TestModelCircuitBreaker:
    """ModelCircuitBreaker 熔断器状态转换测试。"""

    def test_initial_state_is_closed(self) -> None:
        """初始状态应为 CLOSED。"""
        cb = ModelCircuitBreaker("test-model")
        assert cb.state == BreakerState.CLOSED
        assert cb.is_open is False

    def test_transitions_to_open_after_threshold(self) -> None:
        """连续失败达到阈值后应转为 OPEN。"""
        cb = ModelCircuitBreaker("test-model", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == BreakerState.CLOSED  # 还没到阈值
        cb.record_failure()
        assert cb.state == BreakerState.OPEN
        assert cb.is_open is True

    def test_open_transitions_to_half_open_after_timeout(self) -> None:
        """OPEN 状态经过 recovery_timeout 后应转为 HALF_OPEN。"""
        cb = ModelCircuitBreaker(
            "test-model",
            failure_threshold=1,
            recovery_timeout=1,  # 1 秒超时
        )
        cb.record_failure()
        # 内部状态应为 OPEN（直接检查 _state 绕过自动转换）
        assert cb._state == BreakerState.OPEN

        # 模拟等待超时
        cb._last_failure_time = time.monotonic() - 2  # 设为 2 秒前
        assert cb.state == BreakerState.HALF_OPEN

    def test_half_open_to_closed_after_successes(self) -> None:
        """HALF_OPEN 状态下连续成功达到阈值后应转为 CLOSED。"""
        cb = ModelCircuitBreaker(
            "test-model",
            failure_threshold=1,
            recovery_timeout=0,
            success_threshold=2,
        )
        cb.record_failure()
        assert cb.state == BreakerState.HALF_OPEN

        cb.record_success()
        assert cb.state == BreakerState.HALF_OPEN  # 还没到阈值
        cb.record_success()
        assert cb.state == BreakerState.CLOSED

    def test_half_open_to_open_on_failure(self) -> None:
        """HALF_OPEN 状态下任何失败应立即回到 OPEN。"""
        cb = ModelCircuitBreaker(
            "test-model",
            failure_threshold=1,
            recovery_timeout=1,
        )
        cb.record_failure()
        # 模拟超时进入 HALF_OPEN
        cb._last_failure_time = time.monotonic() - 2
        assert cb.state == BreakerState.HALF_OPEN

        cb.record_success()  # 在 HALF_OPEN 中记录一次成功
        cb.record_failure()  # 然后失败
        # 内部状态应为 OPEN（直接检查 _state，因为 recovery_timeout 短可能导致自动转换）
        assert cb._state == BreakerState.OPEN

    def test_reset(self) -> None:
        """手动重置应恢复到 CLOSED。"""
        cb = ModelCircuitBreaker("test-model", failure_threshold=1)
        cb.record_failure()
        assert cb.state == BreakerState.OPEN

        cb.reset()
        assert cb.state == BreakerState.CLOSED
        assert cb.is_open is False

    def test_success_resets_failure_count_when_closed(self) -> None:
        """CLOSED 状态下成功应重置失败计数。"""
        cb = ModelCircuitBreaker("test-model", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # 重置失败计数
        # 现在需要再失败 3 次才能 OPEN
        cb.record_failure()
        cb.record_failure()
        assert cb.state == BreakerState.CLOSED  # 只失败 2 次
        cb.record_failure()
        assert cb.state == BreakerState.OPEN  # 第 3 次


# ---------------------------------------------------------------------------
# ModelSelector 测试
# ---------------------------------------------------------------------------

class TestModelSelector:
    """ModelSelector 模型选择器测试。"""

    def test_select_candidates_returns_enabled_sorted(self) -> None:
        """验证返回已启用、非熔断、按优先级排序的候选列表。"""
        mgr = _make_manager(
            chat=[
                ModelCandidate(model_name="low-prio", priority=10),
                ModelCandidate(model_name="high-prio", priority=0),
                ModelCandidate(model_name="mid-prio", priority=5),
            ],
        )
        selector = ModelSelector(mgr)
        candidates = selector.select_candidates("chat")

        names = [c.model_name for c in candidates]
        assert names == ["high-prio", "mid-prio", "low-prio"]

    def test_filters_out_disabled_candidates(self) -> None:
        """验证过滤掉禁用的候选模型。"""
        mgr = _make_manager(
            chat=[
                ModelCandidate(model_name="enabled", priority=0, enabled=True),
                ModelCandidate(model_name="disabled", priority=1, enabled=False),
            ],
        )
        selector = ModelSelector(mgr)
        candidates = selector.select_candidates("chat")

        assert len(candidates) == 1
        assert candidates[0].model_name == "enabled"

    def test_filters_out_open_breakers(self) -> None:
        """验证过滤掉熔断器为 OPEN 的候选模型。"""
        mgr = _make_manager(
            chat=[
                ModelCandidate(model_name="broken-model", priority=0),
                ModelCandidate(model_name="healthy-model", priority=1),
            ],
        )
        selector = ModelSelector(mgr)

        # 手动让 broken-model 的熔断器打开
        selector.record_failure("broken-model")
        selector.record_failure("broken-model")
        selector.record_failure("broken-model")
        selector.record_failure("broken-model")
        selector.record_failure("broken-model")  # 第 5 次，触发 OPEN

        candidates = selector.select_candidates("chat")
        assert len(candidates) == 1
        assert candidates[0].model_name == "healthy-model"

    def test_all_disabled_raises_exception(self) -> None:
        """所有候选均禁用时应抛出 RemoteException。"""
        mgr = _make_manager(
            chat=[
                ModelCandidate(model_name="m1", enabled=False),
            ],
        )
        selector = ModelSelector(mgr)
        with pytest.raises(RemoteException, match="C3001"):
            selector.select_candidates("chat")

    def test_all_open_raises_exception(self) -> None:
        """所有候选熔断时应抛出 RemoteException。"""
        mgr = _make_manager(
            chat=[
                ModelCandidate(model_name="m1", priority=0),
            ],
        )
        selector = ModelSelector(mgr)

        # 触发熔断
        for _ in range(5):
            selector.record_failure("m1")

        with pytest.raises(RemoteException, match="C3001"):
            selector.select_candidates("chat")

    def test_empty_candidates_raises_exception(self) -> None:
        """无候选模型时应抛出 RemoteException。"""
        mgr = _make_manager(chat=[])
        selector = ModelSelector(mgr)
        with pytest.raises(RemoteException, match="C3001"):
            selector.select_candidates("chat")

    def test_record_success_reopens_model(self) -> None:
        """验证记录成功后，熔断器恢复正常，模型重新可用。"""
        cb_config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=1,  # 1 秒超时进入 HALF_OPEN
            success_threshold=1,
        )
        mgr = _make_manager(
            chat=[
                ModelCandidate(model_name="recovering-model", priority=0),
            ],
            cb_config=cb_config,
        )
        selector = ModelSelector(mgr)

        # 触发熔断
        selector.record_failure("recovering-model")
        breaker = selector._breakers["recovering-model"]
        # 内部状态应为 OPEN
        assert breaker._state == BreakerState.OPEN

        # 模拟等待超时，使状态变为 HALF_OPEN
        breaker._last_failure_time = time.monotonic() - 2
        assert breaker.state == BreakerState.HALF_OPEN

        # 在 HALF_OPEN 下记录成功，触发恢复
        selector.record_success("recovering-model")
        assert breaker.state == BreakerState.CLOSED

        # 现在模型应该重新可用
        candidates = selector.select_candidates("chat")
        assert len(candidates) == 1
        assert candidates[0].model_name == "recovering-model"

    def test_breaker_config_from_model_config(self) -> None:
        """验证熔断器参数来自 ModelConfig.circuit_breaker。"""
        cb_config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=300,
            success_threshold=5,
        )
        mgr = _make_manager(
            chat=[ModelCandidate(model_name="m1")],
            cb_config=cb_config,
        )
        selector = ModelSelector(mgr)

        breaker = selector._get_breaker("m1")
        assert breaker.failure_threshold == 2
        assert breaker.recovery_timeout == 300
        assert breaker.success_threshold == 5

    def test_select_candidates_embedding_type(self) -> None:
        """验证 embedding 任务类型选择。"""
        mgr = _make_manager(
            embedding=[
                ModelCandidate(model_name="embed-1", priority=0),
                ModelCandidate(model_name="embed-2", priority=1),
            ],
        )
        selector = ModelSelector(mgr)
        candidates = selector.select_candidates("embedding")
        assert len(candidates) == 2
        assert candidates[0].model_name == "embed-1"

    def test_priority_sorting_stable(self) -> None:
        """验证相同优先级保持原始顺序（稳定排序）。"""
        mgr = _make_manager(
            chat=[
                ModelCandidate(model_name="first", priority=0),
                ModelCandidate(model_name="second", priority=0),
                ModelCandidate(model_name="third", priority=0),
            ],
        )
        selector = ModelSelector(mgr)
        candidates = selector.select_candidates("chat")
        names = [c.model_name for c in candidates]
        assert names == ["first", "second", "third"]
