"""
追踪模块 —— 基于注解的 RAG 管线追踪框架

提供轻量级的分布式追踪能力，用于监控 RAG 管线各阶段的执行情况：
    - ``@rag_trace_root('name')`` —— 创建根追踪段（Root Span）
    - ``@rag_trace_node('name')`` —— 创建子追踪段（Child Span）

覆盖阶段示例：
    query-rewrite、intent-classify、retrieval、rerank、
    prompt-build、llm-generate

设计要点：
    - ``trace_id`` 通过 ``ContextVar`` 在协程间传播
    - 追踪段以树形结构组织，支持嵌套子段
    - 使用 ``time.monotonic()`` 记录高精度耗时
    - 异常自动捕获，设置 ``status='error'`` 并记录错误信息
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Coroutine, TypeVar, ParamSpec

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 类型变量与别名
# ---------------------------------------------------------------------------

P = ParamSpec("P")
R = TypeVar("R")

# ---------------------------------------------------------------------------
# 追踪数据模型
# ---------------------------------------------------------------------------


class TraceSpan(BaseModel):
    """追踪段数据模型。

    表示 RAG 管线中某一次操作的执行记录，支持嵌套子段形成调用树。

    Attributes:
        span_id:      追踪段唯一标识（UUID）。
        name:         追踪段名称，例如 ``query-rewrite``。
        start_time:   单调时钟起始时间（秒）。
        end_time:     单调时钟结束时间（秒），未结束时为 ``None``。
        duration_ms:  执行耗时（毫秒），未结束时为 ``None``。
        status:       执行状态：``'running'`` | ``'ok'`` | ``'error'``。
        error_message:错误信息，仅在 ``status='error'`` 时有值。
        children:     子追踪段列表，构成调用树。
        metadata:     附加元数据字典，用于存储自定义信息。
    """

    span_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    start_time: float = Field(default_factory=time.monotonic)
    end_time: float | None = None
    duration_ms: float | None = None
    status: str = "running"
    error_message: str | None = None
    children: list[TraceSpan] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config: dict[str, Any] = {"arbitrary_types_allowed": True}

    def finish(self, status: str = "ok", error_message: str | None = None) -> None:
        """结束当前追踪段，计算耗时并设置最终状态。

        Args:
            status:       最终状态，默认 ``'ok'``。
            error_message:错误信息（可选）。
        """
        self.end_time = time.monotonic()
        self.duration_ms = (self.end_time - self.start_time) * 1000.0
        self.status = status
        self.error_message = error_message

    def to_summary_dict(self) -> dict[str, Any]:
        """将追踪段转换为摘要字典（包含子段的递归结构）。

        Returns:
            包含追踪段完整信息的字典。
        """
        return {
            "span_id": self.span_id,
            "name": self.name,
            "duration_ms": round(self.duration_ms, 3) if self.duration_ms is not None else None,
            "status": self.status,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "children": [child.to_summary_dict() for child in self.children],
        }


# ---------------------------------------------------------------------------
# 上下文管理
# ---------------------------------------------------------------------------

# 当前活跃的追踪段（用于嵌套子段的父子关系）
_trace_context: ContextVar[TraceSpan | None] = ContextVar("_trace_context", default=None)

# trace_id 传播变量，跨协程保持一致的追踪标识
_trace_id_var: ContextVar[str] = ContextVar("_trace_id_var", default="")


# ---------------------------------------------------------------------------
# 公共访问函数
# ---------------------------------------------------------------------------


def get_trace_id() -> str:
    """获取当前追踪标识。

    若当前上下文中已有 ``trace_id`` 则直接返回；
    否则生成一个新的 UUID 并绑定到当前上下文。

    Returns:
        当前追踪标识字符串。
    """
    trace_id = _trace_id_var.get("")
    if not trace_id:
        trace_id = uuid.uuid4().hex
        _trace_id_var.set(trace_id)
    return trace_id


def get_current_span() -> TraceSpan | None:
    """获取当前上下文中的活跃追踪段。

    Returns:
        当前活跃的 ``TraceSpan``，若无则返回 ``None``。
    """
    return _trace_context.get(None)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _log_span(span: TraceSpan, *, is_root: bool) -> None:
    """通过日志模块输出追踪段的摘要信息。

    Args:
        span:     已完成的追踪段。
        is_root:  是否为根追踪段。
    """
    trace_id = _trace_id_var.get("")
    level = logging.DEBUG
    role = "根追踪段" if is_root else "子追踪段"
    summary = span.to_summary_dict()

    if span.status == "error":
        level = logging.DEBUG  # 错误也在 DEBUG 级别，保持一致性；可根据需要调整为 WARNING
        logger.log(
            level,
            "[追踪] %s | trace_id=%s | name=%s | status=%s | duration=%.3fms | error=%s",
            role,
            trace_id,
            span.name,
            span.status,
            span.duration_ms or 0.0,
            span.error_message,
        )
    else:
        logger.log(
            level,
            "[追踪] %s | trace_id=%s | name=%s | status=%s | duration=%.3fms",
            role,
            trace_id,
            span.name,
            span.status,
            span.duration_ms or 0.0,
        )

    # 根追踪段完成时，输出完整的追踪树
    if is_root:
        logger.log(logging.DEBUG, "[追踪] 完整追踪树 | trace_id=%s | data=%s", trace_id, summary)


# ---------------------------------------------------------------------------
# 装饰器：根追踪段
# ---------------------------------------------------------------------------


def rag_trace_root(name: str) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
    """创建根追踪段的异步装饰器。

    用于 RAG 管线的入口函数（如完整的 RAG 请求处理），会创建一个根追踪段
    并在完成后输出完整的追踪树日志。

    功能：
        - 创建 ``TraceSpan`` 作为根段
        - 设置 ``trace_id`` 到上下文
        - 记录执行耗时和状态
        - 异常自动捕获，设置 ``status='error'``
        - 完成后通过 ``logging`` 模块输出追踪摘要

    Args:
        name: 追踪段名称，例如 ``'rag-pipeline'``。

    Returns:
        异步装饰器函数。

    Example::

        @rag_trace_root('rag-pipeline')
        async def handle_query(query: str) -> str:
            ...
    """

    def decorator(func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # 创建根追踪段
            span = TraceSpan(name=name)

            # 生成新的 trace_id 并绑定到上下文
            trace_id = uuid.uuid4().hex
            _trace_id_var.set(trace_id)

            # 设置当前活跃追踪段
            token = _trace_context.set(span)

            try:
                logger.debug(
                    "[追踪] 根追踪段开始 | trace_id=%s | name=%s | span_id=%s",
                    trace_id,
                    name,
                    span.span_id,
                )
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
                _trace_context.reset(token)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 装饰器：子追踪段
# ---------------------------------------------------------------------------


def rag_trace_node(name: str) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
    """创建子追踪段的异步装饰器。

    用于 RAG 管线中的各个阶段（如检索、重排、生成等），会在当前活跃追踪段
    下创建子段并追加到父段的 ``children`` 列表中。

    若当前无活跃追踪段（即未在 ``@rag_trace_root`` 包裹的上下文中调用），
    则仍会创建追踪段并记录日志，但不会挂载到任何父段。

    功能：
        - 在父追踪段下创建子 ``TraceSpan``
        - 记录执行耗时和状态
        - 异常自动捕获，设置 ``status='error'``
        - 完成后通过 ``logging`` 模块输出追踪摘要

    Args:
        name: 追踪段名称，例如 ``'retrieval'``、``'llm-generate'``。

    Returns:
        异步装饰器函数。

    Example::

        @rag_trace_node('retrieval')
        async def retrieve_documents(query: str) -> list[Document]:
            ...
    """

    def decorator(func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # 创建子追踪段
            span = TraceSpan(name=name)

            # 获取当前父追踪段
            parent_span = _trace_context.get(None)

            if parent_span is not None:
                # 将子段追加到父段的 children 列表
                parent_span.children.append(span)
                # 切换当前上下文为子段（支持进一步嵌套）
                token = _trace_context.set(span)
            else:
                # 无父段时，仍创建追踪段但设为当前上下文以便嵌套的子段能找到父段
                token = _trace_context.set(span)

            try:
                logger.debug(
                    "[追踪] 子追踪段开始 | trace_id=%s | name=%s | span_id=%s | parent=%s",
                    _trace_id_var.get(""),
                    name,
                    span.span_id,
                    parent_span.span_id if parent_span else "无",
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
                _trace_context.reset(token)

        return wrapper

    return decorator
