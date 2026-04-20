"""
中间件模块 —— 请求链路追踪、上下文注入、全局异常处理

提供三组 Starlette 中间件：
    - ``TraceMiddleware``           —— 为每个请求生成/传播 trace_id
    - ``RequestContextMiddleware``  —— 提取请求头中的用户信息到 ContextVar
    - ``ExceptionHandlerMiddleware``—— 捕获未处理异常并返回统一格式

中间件执行顺序（最后添加最先执行）：
    TraceMiddleware → RequestContextMiddleware → ExceptionHandlerMiddleware → 路由
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragent.common.context import UserContext, set_user_context, clear_user_context
from ragent.common.logging import get_logger
from ragent.common.response import Result
from ragent.common.trace import _trace_id_var, get_tracer

logger: logging.Logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 链路追踪中间件
# ---------------------------------------------------------------------------


class TraceMiddleware(BaseHTTPMiddleware):
    """链路追踪中间件 —— 为每个请求生成 trace_id。

    若请求头中携带 ``X-Trace-Id``，则复用该值以支持分布式追踪传播；
    否则自动生成一个新的 UUID hex 作为 trace_id。

    生成的 trace_id 会：
        1. 写入 ``request.state.trace_id`` 供后续逻辑读取
        2. 写入 ContextVar ``_trace_id_var`` 以便日志过滤器自动注入
        3. 写入响应头 ``X-Trace-Id`` 返回给调用方
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        """拦截请求，用 OTel span 注入 trace_id。

        Args:
            request:  Starlette 请求对象。
            call_next: 下一个中间件或路由处理函数。

        Returns:
            处理后的响应对象。
        """
        tracer = get_tracer()
        span_name = f"HTTP {request.method} {request.url.path}"

        with tracer.start_as_current_span(span_name) as otel_span:
            # 从请求头或新生成 trace_id
            incoming_tid = request.headers.get("X-Trace-Id", "")
            trace_id: str = incoming_tid or format(otel_span.get_span_context().trace_id, "032x")

            request.state.trace_id = trace_id
            _trace_id_var.set(trace_id)

            otel_span.set_attribute("http.method", request.method)
            otel_span.set_attribute("http.url", str(request.url))
            otel_span.set_attribute("trace_id", trace_id)

            try:
                response: Response = await call_next(request)
                response.headers["X-Trace-Id"] = trace_id
                otel_span.set_attribute("http.status_code", response.status_code)
                return response
            except Exception as exc:
                otel_span.set_attribute("error", True)
                otel_span.record_exception(exc)
                raise
            finally:
                _trace_id_var.set("")


# ---------------------------------------------------------------------------
# 请求上下文中间件
# ---------------------------------------------------------------------------


class RequestContextMiddleware(BaseHTTPMiddleware):
    """请求上下文中间件 —— 提取用户信息到 ContextVar。

    从请求头中提取以下字段并构造 ``UserContext``：
        - ``X-User-Id``   → ``user_id``
        - ``X-Username``  → ``username``
        - ``X-User-Role`` → ``role``

    若缺少必要头部（如 ``X-User-Id``），则不设置上下文，请求继续正常处理。
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        """拦截请求，提取用户上下文。

        Args:
            request:  Starlette 请求对象。
            call_next: 下一个中间件或路由处理函数。

        Returns:
            处理后的响应对象。
        """
        user_id: str | None = request.headers.get("X-User-Id")
        username: str = request.headers.get("X-Username", "anonymous")
        role: str = request.headers.get("X-User-Role", "user")

        if user_id:
            user_ctx = UserContext(user_id=user_id, username=username, role=role)
            set_user_context(user_ctx)
            request.state.user = user_ctx
        else:
            request.state.user = None

        try:
            return await call_next(request)
        finally:
            clear_user_context()


# ---------------------------------------------------------------------------
# 全局异常处理中间件
# ---------------------------------------------------------------------------


class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    """全局异常处理中间件 —— 捕获未处理异常返回统一格式。

    拦截路由处理中未被显式捕获的异常，将其转换为 ``Result.error()`` 格式
    的 JSON 响应，同时记录错误日志。

    响应格式::

        {
            "code": 500,
            "message": "内部服务错误",
            "data": null,
            "trace_id": "xxx",
            "timestamp": 1234567890.123
        }
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        """拦截请求，捕获异常。

        Args:
            request:  Starlette 请求对象。
            call_next: 下一个中间件或路由处理函数。

        Returns:
            正常响应或错误格式的 JSON 响应。
        """
        try:
            return await call_next(request)
        except Exception as exc:
            trace_id: str = getattr(request.state, "trace_id", "-")
            logger.exception(
                "未处理异常 | trace_id=%s | path=%s | error=%s",
                trace_id,
                request.url.path,
                exc,
            )
            result: Result = Result.error(
                code=500,
                message="内部服务错误",
                trace_id=trace_id,
            )
            return JSONResponse(
                status_code=500,
                content=result.model_dump(),
            )
