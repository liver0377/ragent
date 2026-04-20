"""
追踪模块 —— 基于 OpenTelemetry SDK 的 RAG 管线追踪框架

提供与 OTel 兼容的分布式追踪能力：
    - 自动初始化 TracerProvider + ConsoleSpanExporter
    - ``get_tracer()`` — 获取 OTel Tracer 实例
    - ``get_trace_id()`` — 获取当前 trace_id（兼容旧接口）
    - ``TraceSpan`` — OTel Span 的兼容适配器
    - ``rag_trace_root()`` / ``rag_trace_node()`` — 装饰器（向后兼容）

trace_id 通过 OTel Context 在协程间自动传播。
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Coroutine, TypeVar, ParamSpec

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 类型变量
# ---------------------------------------------------------------------------

P = ParamSpec("P")
R = TypeVar("R")

# ---------------------------------------------------------------------------
# OTel 初始化
# ---------------------------------------------------------------------------

# 创建 Resource（标识服务）
_resource = Resource.create({
    "service.name": "ragent",
    "service.version": "1.0.0",
})

# TracerProvider + Console Exporter
_provider = TracerProvider(resource=_resource)
_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(_provider)

# 全局 tracer
_tracer = trace.get_tracer("ragent.rag", "1.0.0")


def get_tracer() -> trace.Tracer:
    """获取全局 OTel Tracer 实例。"""
    return _tracer


# ---------------------------------------------------------------------------
# 兼容层：ContextVar（供 middleware / logging 使用）
# ---------------------------------------------------------------------------

# 保持 _trace_id_var 兼容，但值从 OTel context 同步
_trace_id_var: ContextVar[str] = ContextVar("_trace_id_var", default="")


def _sync_trace_id_from_otel() -> str:
    """从 OTel 当前 context 提取 trace_id，同步到 ContextVar。"""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id != 0:
        tid = format(ctx.trace_id, "032x")
        _trace_id_var.set(tid)
        return tid
    return _trace_id_var.get("")


# ---------------------------------------------------------------------------
# 公共访问函数
# ---------------------------------------------------------------------------


def get_trace_id() -> str:
    """获取当前追踪标识（兼容旧接口）。

    优先从 OTel context 获取，回退到 ContextVar。
    """
    return _sync_trace_id_from_otel()


def get_current_span() -> trace.Span | None:
    """获取当前 OTel Span。"""
    span = trace.get_current_span()
    if not span.is_recording():
        return None
    return span


# ---------------------------------------------------------------------------
# 兼容层：TraceSpan 适配器
# ---------------------------------------------------------------------------


class TraceSpan:
    """OTel Span 的兼容适配器，保持旧 API 接口不变。

    内部持有一个真正的 OTel Span，对外暴露 name/status/duration_ms 等属性。
    """

    def __init__(self, name: str, *, parent: trace.Span | None = None) -> None:
        self.name = name
        self._otel_span: trace.Span | None = None
        self._start_time = time.monotonic()
        self.duration_ms: float | None = None
        self.status: str = "running"
        self.error_message: str | None = None
        self.children: list[TraceSpan] = []
        self.metadata: dict[str, Any] = {}

        # 创建 OTel span
        ctx_token = None
        if parent is not None:
            ctx = trace.set_span_in_context(parent)
            ctx_token = _otel_ctx.set(ctx)

        self._otel_span = _tracer.start_span(name)
        self._ctx_token = _otel_ctx.set(trace.set_span_in_context(self._otel_span))

        if ctx_token is not None:
            _otel_ctx.reset(ctx_token)

    @property
    def span_id(self) -> str:
        if self._otel_span:
            ctx = self._otel_span.get_span_context()
            return format(ctx.span_id, "016x")
        return ""

    def set_attribute(self, key: str, value: Any) -> None:
        """设置 span 属性（元数据）。"""
        self.metadata[key] = value
        if self._otel_span:
            self._otel_span.set_attribute(key, str(value))

    def finish(self, status: str = "ok", error_message: str | None = None) -> None:
        """结束追踪段。"""
        end_time = time.monotonic()
        self.duration_ms = (end_time - self._start_time) * 1000.0
        self.status = status
        self.error_message = error_message

        if self._otel_span:
            from opentelemetry.trace import Status, StatusCode
            if status == "error":
                self._otel_span.set_status(Status(StatusCode.ERROR, error_message or ""))
                self._otel_span.record_exception(Exception(error_message or "unknown"))
            else:
                self._otel_span.set_status(Status(StatusCode.OK))
            self._otel_span.end()

    def to_summary_dict(self) -> dict[str, Any]:
        """转换为摘要字典（兼容旧接口）。"""
        return {
            "span_id": self.span_id,
            "name": self.name,
            "duration_ms": round(self.duration_ms, 3) if self.duration_ms is not None else None,
            "status": self.status,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "children": [c.to_summary_dict() for c in self.children],
        }


# OTel context 存储（用于父子 span 传播）
_otel_ctx: ContextVar[Any] = ContextVar("_otel_ctx", default=None)

# 旧 _trace_context 兼容（保持 import 不报错）
_trace_context: ContextVar[TraceSpan | None] = ContextVar("_trace_context", default=None)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _log_span(span: TraceSpan, *, is_root: bool) -> None:
    """通过日志输出追踪摘要。"""
    trace_id = get_trace_id()
    level = logging.DEBUG
    role = "根追踪段" if is_root else "子追踪段"

    if span.status == "error":
        logger.log(
            level,
            "[OTel追踪] %s | trace_id=%s | name=%s | status=%s | duration=%.3fms | error=%s",
            role, trace_id, span.name, span.status, span.duration_ms or 0.0, span.error_message,
        )
    else:
        logger.log(
            level,
            "[OTel追踪] %s | trace_id=%s | name=%s | status=%s | duration=%.3fms",
            role, trace_id, span.name, span.status, span.duration_ms or 0.0,
        )

    if is_root:
        logger.log(logging.DEBUG, "[OTel追踪] 完整追踪树 | trace_id=%s | data=%s", trace_id, span.to_summary_dict())


# ---------------------------------------------------------------------------
# 装饰器：根追踪段（兼容旧接口）
# ---------------------------------------------------------------------------


def rag_trace_root(name: str) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
    """创建根追踪段的异步装饰器（基于 OTel）。"""

    def decorator(func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            span = TraceSpan(name=name)
            trace_id = uuid.uuid4().hex
            _trace_id_var.set(trace_id)
            ctx_token = _trace_context.set(span)

            try:
                logger.debug("[OTel追踪] 根追踪段开始 | trace_id=%s | name=%s", trace_id, name)
                result = await func(*args, **kwargs)
                span.finish(status="ok")
            except Exception as exc:
                span.finish(status="error", error_message=str(exc))
                _log_span(span, is_root=True)
                raise
            else:
                _log_span(span, is_root=True)
                return result
            finally:
                _trace_context.reset(ctx_token)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 装饰器：子追踪段（兼容旧接口）
# ---------------------------------------------------------------------------


def rag_trace_node(name: str) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
    """创建子追踪段的异步装饰器（基于 OTel）。"""

    def decorator(func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            parent_span = _trace_context.get(None)
            span = TraceSpan(name=name, parent=parent_span._otel_span if parent_span else None)

            if parent_span is not None:
                parent_span.children.append(span)

            ctx_token = _trace_context.set(span)

            try:
                logger.debug(
                    "[OTel追踪] 子追踪段开始 | trace_id=%s | name=%s | parent=%s",
                    get_trace_id(), name, parent_span.span_id if parent_span else "无",
                )
                result = await func(*args, **kwargs)
                span.finish(status="ok")
            except Exception as exc:
                span.finish(status="error", error_message=str(exc))
                _log_span(span, is_root=False)
                raise
            else:
                _log_span(span, is_root=False)
                return result
            finally:
                _trace_context.reset(ctx_token)

        return wrapper

    return decorator
