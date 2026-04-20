"""
大整数精度保护中间件。

JavaScript 的 Number 类型最大安全整数为 2^53-1 (9007199254740991)。
项目使用 Snowflake ID（18-19 位数字），超出 JS 安全范围，
导致 JSON.parse() 后末尾精度丢失（如 304603331693641728 → 304603331693641700）。

本中间件拦截所有 JSON 响应，将超过 JS 安全范围的大整数转为字符串。
"""

from __future__ import annotations

import json
import re

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

_JS_MAX_SAFE_INT = 9007199254740991  # 2^53 - 1

# 匹配 JSON 中的大整数（超过 16 位数字），排除已被引号包裹的字符串
# 匹配模式：冒号/方括号后的数字，或逗号后的数字
_LARGE_INT_PATTERN = re.compile(
    r'(?<=[\[:\,])\s*(\d{16,19})\s*(?=[\}\]\,])',
)


class LargeIntMiddleware(BaseHTTPMiddleware):
    """将 JSON 响应中超 JS 安全范围的大整数转为字符串。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        # 只处理 JSON 响应
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # 读取原始响应体
        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                body_chunks.append(chunk.encode("utf-8"))
            else:
                body_chunks.append(chunk)
        body = b"".join(body_chunks)

        # 将大整数转为字符串
        text = body.decode("utf-8")
        converted = _LARGE_INT_PATTERN.sub(r'"\1"', text)

        return Response(
            content=converted.encode("utf-8"),
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type="application/json",
        )
