"""
IP 级别速率限制中间件。

基于 Redis 滑动窗口实现，按 IP + 路径前缀 分组限流。
保护以下关键端点：
    - POST /api/v1/auth/register  —— 注册（防刷号）
    - POST /api/v1/auth/login     —— 登录（防暴力破解）
    - POST /api/v1/chat           —— 聊天（防滥用）
    - POST /api/v1/documents/upload —— 上传（防大量上传）

Redis Key 格式: ``ratelimit:{ip}:{prefix}``，值为 sorted set（timestamp → request_id）。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragent.common.logging import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
#  限流规则定义
# --------------------------------------------------------------------------- #

class RateLimitRule:
    """单条限流规则。"""

    __slots__ = ("prefix", "max_requests", "window_seconds", "methods")

    def __init__(
        self,
        prefix: str,
        max_requests: int,
        window_seconds: int,
        methods: set[str] | None = None,
    ):
        self.prefix = prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.methods = methods  # None = 所有方法


# 默认限流规则
DEFAULT_RULES: list[RateLimitRule] = [
    RateLimitRule("/api/v1/auth/register", max_requests=5, window_seconds=60, methods={"POST"}),
    RateLimitRule("/api/v1/auth/login", max_requests=10, window_seconds=60, methods={"POST"}),
    RateLimitRule("/api/v1/chat", max_requests=20, window_seconds=60, methods={"POST"}),
    RateLimitRule("/api/v1/documents/upload", max_requests=10, window_seconds=60, methods={"POST"}),
]


# --------------------------------------------------------------------------- #
#  限流中间件
# --------------------------------------------------------------------------- #

class RateLimitMiddleware(BaseHTTPMiddleware):
    """IP 级别速率限制中间件。

    检查每个请求是否超过对应路径的限流阈值。
    超限返回 HTTP 429 Too Many Requests。
    """

    def __init__(self, app: Any, rules: list[RateLimitRule] | None = None):
        super().__init__(app)
        self._rules = rules or DEFAULT_RULES

    def _match_rule(self, request: Request) -> RateLimitRule | None:
        """匹配请求路径到限流规则。"""
        path = request.url.path
        method = request.method.upper()
        for rule in self._rules:
            if path.startswith(rule.prefix):
                if rule.methods is None or method in rule.methods:
                    return rule
        return None

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """获取客户端 IP（支持反向代理头）。"""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        if request.client:
            return request.client.host
        return "unknown"

    async def _check_rate_limit(self, ip: str, rule: RateLimitRule) -> bool:
        """检查是否超限。返回 True 表示允许，False 表示超限。"""
        try:
            from ragent.common.redis_manager import get_redis_manager
            manager = get_redis_manager()
            redis = manager.get_redis()
        except Exception:
            # Redis 不可用时不阻塞请求
            logger.warning("Redis 不可用，跳过限流检查")
            return True

        now = time.time()
        window_start = now - rule.window_seconds
        key = f"ratelimit:{ip}:{rule.prefix}"
        member = f"{now}:{uuid.uuid4().hex[:8]}"

        pipe = redis.pipeline(transaction=True)
        # 移除窗口外的旧记录
        pipe.zremrangebyscore(key, "-inf", window_start)
        # 添加当前请求
        pipe.zadd(key, {member: now})
        # 统计窗口内请求数
        pipe.zcard(key)
        # 设置 key 过期时间（略大于窗口，兜底清理）
        pipe.expire(key, rule.window_seconds + 10)
        results = await pipe.execute()

        count = results[2]  # zcard 结果
        return count <= rule.max_requests

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rule = self._match_rule(request)
        if rule is None:
            return await call_next(request)

        ip = self._get_client_ip(request)
        allowed = await self._check_rate_limit(ip, rule)

        if not allowed:
            logger.warning("速率限制触发: ip=%s, path=%s, limit=%d/%ds",
                           ip, request.url.path, rule.max_requests, rule.window_seconds)
            return JSONResponse(
                status_code=429,
                content={
                    "code": 429,
                    "message": f"请求过于频繁，请 {rule.window_seconds} 秒后重试",
                    "data": None,
                    "trace_id": None,
                    "timestamp": time.time(),
                },
                headers={"Retry-After": str(rule.window_seconds)},
            )

        return await call_next(request)
