"""
自定义 JSON Response —— 自动处理大整数精度问题。

所有 API 响应通过此类输出，超大整数（Snowflake ID）自动序列化为字符串。
"""

from __future__ import annotations

import json
from typing import Any

from starlette.responses import JSONResponse

from ragent.common.json_utils import LargeIntJSONEncoder


class SafeJSONResponse(JSONResponse):
    """自动将超大整数转为字符串的 JSON Response。"""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            cls=LargeIntJSONEncoder,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
