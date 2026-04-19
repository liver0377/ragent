"""模型选择器模块 —— 根据任务类型选择并排序可用模型候选。

核心职责：
    1. 过滤掉已禁用的候选模型
    2. 过滤掉熔断器处于 OPEN 状态的模型
    3. 按优先级（priority 升序）排序后返回候选列表
    4. 提供成功/失败回调以更新熔断器内部状态
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import TYPE_CHECKING

from ragent.common.exceptions import RemoteException

if TYPE_CHECKING:
    from ragent.infra.ai.models import ModelCandidate, ModelConfigManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 熔断器状态枚举
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    """熔断器状态。"""

    CLOSED = "closed"       # 正常（闭合）
    OPEN = "open"           # 熔断（打开，拒绝请求）
    HALF_OPEN = "half_open" # 半开（允许少量探测请求）


# ---------------------------------------------------------------------------
# 简易模型级熔断器
# ---------------------------------------------------------------------------

class ModelCircuitBreaker:
    """模型级熔断器 —— 手动管理三状态（CLOSED / OPEN / HALF_OPEN）。

    状态转换规则：
        CLOSED  → 连续失败次数 >= failure_threshold → OPEN
        OPEN    → 经过 recovery_timeout 后 → HALF_OPEN
        HALF_OPEN → 连续成功次数 >= success_threshold → CLOSED
        HALF_OPEN → 任意一次失败 → OPEN

    Attributes:
        model_name:        模型名称（用于日志）
        failure_threshold: 连续失败阈值
        recovery_timeout:  恢复超时（秒）
        success_threshold: 半开状态下连续成功阈值
    """

    def __init__(
        self,
        model_name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        success_threshold: int = 3,
    ) -> None:
        self.model_name: str = model_name
        self.failure_threshold: int = failure_threshold
        self.recovery_timeout: int = recovery_timeout
        self.success_threshold: int = success_threshold

        self._state: BreakerState = BreakerState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._last_failure_time: float = 0.0

    @property
    def state(self) -> BreakerState:
        """获取当前熔断器状态（自动检查是否应从 OPEN 转为 HALF_OPEN）。"""
        if self._state == BreakerState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = BreakerState.HALF_OPEN
                self._success_count = 0
                logger.info(
                    "熔断器 [%s] 从 OPEN 转为 HALF_OPEN（已等待 %.1fs）",
                    self.model_name,
                    elapsed,
                )
        return self._state

    @property
    def is_open(self) -> bool:
        """熔断器是否处于 OPEN 状态（不可用）。"""
        return self.state == BreakerState.OPEN

    def record_success(self) -> None:
        """记录一次成功调用，可能触发状态转换。"""
        if self._state == BreakerState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = BreakerState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                logger.info("熔断器 [%s] 从 HALF_OPEN 转为 CLOSED", self.model_name)
        elif self._state == BreakerState.CLOSED:
            # 正常状态下成功，重置失败计数
            self._failure_count = 0

    def record_failure(self) -> None:
        """记录一次失败调用，可能触发状态转换。"""
        if self._state == BreakerState.HALF_OPEN:
            # 半开状态下失败，立即回到 OPEN
            self._state = BreakerState.OPEN
            self._last_failure_time = time.monotonic()
            self._success_count = 0
            logger.warning("熔断器 [%s] 从 HALF_OPEN 回到 OPEN（探测失败）", self.model_name)
        elif self._state == BreakerState.CLOSED:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self.failure_threshold:
                self._state = BreakerState.OPEN
                logger.warning(
                    "熔断器 [%s] 从 CLOSED 转为 OPEN（连续失败 %d 次）",
                    self.model_name,
                    self._failure_count,
                )

    def reset(self) -> None:
        """重置熔断器到 CLOSED 状态。"""
        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        logger.info("熔断器 [%s] 已手动重置为 CLOSED", self.model_name)


# ---------------------------------------------------------------------------
# 模型选择器
# ---------------------------------------------------------------------------

class ModelSelector:
    """模型选择器 —— 根据任务类型选择可用模型候选列表。

    使用方式::

        from ragent.infra.ai.models import ModelConfigManager
        from ragent.infra.ai.model_selector import ModelSelector

        mgr = ModelConfigManager()
        selector = ModelSelector(mgr)

        # 获取可用候选列表
        candidates = selector.select_candidates("chat")

        # 调用成功/失败后更新熔断器状态
        selector.record_success("glm-4-flash")
        selector.record_failure("glm-4-flash")
    """

    def __init__(self, config_manager: ModelConfigManager) -> None:
        """初始化模型选择器。

        Args:
            config_manager: 模型配置管理器实例。
        """
        self._config_manager = config_manager
        self._breakers: dict[str, ModelCircuitBreaker] = {}

    # ---- 公共接口 ----

    def select_candidates(self, task_type: str) -> list[ModelCandidate]:
        """选择候选模型列表。

        执行步骤：
            1. 从配置管理器获取指定任务类型的全部候选
            2. 过滤掉 ``enabled=False`` 的候选
            3. 过滤掉熔断器处于 OPEN 状态的候选
            4. 按 ``priority`` 升序排序

        Args:
            task_type: 任务类型，如 ``"chat"``、``"embedding"``、``"rerank"``。

        Returns:
            list[ModelCandidate]: 排序后的可用候选列表。

        Raises:
            RemoteException: 当所有候选均不可用时抛出。
        """
        candidates = self._config_manager.get_candidates(task_type)

        # 步骤 2：过滤掉禁用的候选
        enabled = [c for c in candidates if c.enabled]
        if not enabled:
            raise RemoteException(
                error_code="C3001",
                message=f"任务类型 {task_type!r} 没有可用的已启用模型候选",
            )

        # 步骤 3：过滤掉熔断器 OPEN 的候选
        available = [c for c in enabled if not self._get_breaker(c.model_name).is_open]
        if not available:
            raise RemoteException(
                error_code="C3001",
                message=f"任务类型 {task_type!r} 的所有候选模型均处于熔断状态",
            )

        # 步骤 4：按优先级排序
        available.sort(key=lambda c: c.priority)

        logger.debug(
            "任务类型 %s: 可用候选 %s",
            task_type,
            [c.model_name for c in available],
        )
        return available

    def record_success(self, model_name: str) -> None:
        """记录模型调用成功。

        外部模块在模型调用成功后应调用此方法，
        以便熔断器在 HALF_OPEN 状态下逐步恢复正常。

        Args:
            model_name: 模型名称。
        """
        breaker = self._get_breaker(model_name)
        breaker.record_success()
        logger.debug("模型 %s 调用成功，熔断器状态: %s", model_name, breaker.state.value)

    def record_failure(self, model_name: str) -> None:
        """记录模型调用失败。

        外部模块在模型调用失败后应调用此方法，
        以便熔断器累计失败次数并可能进入 OPEN 状态。

        Args:
            model_name: 模型名称。
        """
        breaker = self._get_breaker(model_name)
        breaker.record_failure()
        logger.debug("模型 %s 调用失败，熔断器状态: %s", model_name, breaker.state.value)

    # ---- 内部辅助 ----

    def _get_breaker(self, model_name: str) -> ModelCircuitBreaker:
        """获取或创建指定模型的熔断器实例。

        熔断器参数来自 ``ModelConfig.circuit_breaker`` 配置。

        Args:
            model_name: 模型名称。

        Returns:
            ModelCircuitBreaker: 对应的熔断器实例。
        """
        if model_name not in self._breakers:
            cb_config = self._config_manager.config.circuit_breaker
            self._breakers[model_name] = ModelCircuitBreaker(
                model_name=model_name,
                failure_threshold=cb_config.failure_threshold,
                recovery_timeout=cb_config.recovery_timeout,
                success_threshold=cb_config.success_threshold,
            )
        return self._breakers[model_name]
