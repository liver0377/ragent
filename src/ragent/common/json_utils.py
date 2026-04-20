"""
大整数 JSON 序列化保护。

JavaScript Number 安全整数范围: -(2^53 - 1) ~ 2^53 - 1。
Snowflake ID（18-19 位）超出此范围，JSON.parse 后精度丢失。

解决方案：自定义 JSON serializer，将超出 JS 安全范围的大整数自动序列化为字符串。
前端接收到字符串类型的 ID，用字符串传递即可。
"""

from __future__ import annotations

import json
import math
from typing import Any

# JS Number.MAX_SAFE_INTEGER = 2^53 - 1
_JS_MAX_SAFE_INT = 9007199254740991


def _convert_large_ints(obj: Any) -> Any:
    """递归遍历数据结构，将超大整数转为字符串。"""
    if isinstance(obj, int) and abs(obj) > _JS_MAX_SAFE_INT:
        return str(obj)
    if isinstance(obj, dict):
        return {k: _convert_large_ints(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_convert_large_ints(item) for item in obj]
    return obj


class LargeIntJSONEncoder(json.JSONEncoder):
    """JSON encoder that converts integers exceeding JS safe range to strings."""

    def default(self, obj: Any) -> Any:
        return super().default(obj)

    def encode(self, obj: Any) -> str:
        obj = _convert_large_ints(obj)
        return super().encode(obj)

    def iterencode(self, obj: Any, _one_shot: bool = True):  # type: ignore[override]
        obj = _convert_large_ints(obj)
        return super().iterencode(obj, _one_shot)
