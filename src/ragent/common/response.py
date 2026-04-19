"""
统一响应体模块 —— 标准化 API 返回格式

为所有 HTTP 接口提供一致的响应结构，包含状态码、消息、数据、
追踪 ID 和时间戳，便于前端统一处理以及日志追踪。

典型用法::

    from ragent.common.response import Result, success, error

    # 成功响应
    return Result.success(data={"id": 1, "name": "test"})

    # 错误响应
    return Result.error(code=1001, message="参数缺失")

    # 快捷方式
    return success({"items": []})
    return error(1001, "参数缺失")
"""

from __future__ import annotations

import time
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from ragent.common.exceptions import BaseError

T = TypeVar("T")


# ---------------------------------------------------------------------------
# 通用结果类
# ---------------------------------------------------------------------------

class Result(BaseModel, Generic[T]):
    """统一 API 响应体。

    Attributes:
        code:      状态码，``0`` 表示成功，非零表示错误。
        message:   人类可读的状态描述。
        data:      业务数据负载，泛型类型。
        trace_id:  请求追踪 ID，用于分布式链路追踪，可选。
        timestamp: 响应生成时的 Unix 时间戳。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: int = 0
    message: str = "success"
    data: T | None = None
    trace_id: str | None = None
    timestamp: float = time.time()

    # ---- 静态工厂方法 ----

    @staticmethod
    def success(
        data: T | None = None,
        message: str = "success",
    ) -> Result[T]:
        """创建成功响应。

        Args:
            data:    业务数据，默认为 ``None``。
            message: 状态描述，默认为 ``'success'``。

        Returns:
            包含成功状态和数据的 ``Result`` 实例。
        """
        return Result[T](code=0, message=message, data=data, timestamp=time.time())

    @staticmethod
    def error(
        code: int,
        message: str,
        trace_id: str | None = None,
    ) -> Result[Any]:
        """创建错误响应。

        Args:
            code:     错误码（非零）。
            message:  错误描述信息。
            trace_id: 追踪 ID，可选。

        Returns:
            包含错误信息的 ``Result`` 实例。
        """
        return Result(code=code, message=message, data=None, trace_id=trace_id, timestamp=time.time())

    @staticmethod
    def from_exception(exc: BaseError) -> Result[Any]:
        """从 ``BaseError`` 异常实例创建错误响应。

        将 ``error_code`` 映射为响应的 ``code`` 字段（转为整数哈希值），
        同时将异常信息写入 ``message``。

        Args:
            exc: ``ragent.common.exceptions.BaseError`` 的子类实例。

        Returns:
            包含异常信息的 ``Result`` 实例。
        """
        # 使用 error_code 的哈希值作为整数错误码
        numeric_code = abs(hash(exc.error_code)) % (10 ** 8)
        return Result(
            code=numeric_code,
            message=exc.message,
            data=None,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# 分页结果类
# ---------------------------------------------------------------------------

class PaginationResult(Result[T]):
    """带分页信息的响应体。

    继承 ``Result`` 的所有字段，额外携带分页元数据。

    Attributes:
        total:     总记录数。
        page:      当前页码（从 1 开始）。
        page_size: 每页记录数。
        has_more:  是否还有更多数据。
    """

    total: int = 0
    page: int = 1
    page_size: int = 20
    has_more: bool = False

    @staticmethod
    def success(
        data: T | None = None,
        message: str = "success",
        total: int = 0,
        page: int = 1,
        page_size: int = 20,
        has_more: bool = False,
    ) -> PaginationResult[T]:
        """创建带分页信息的成功响应。

        Args:
            data:      当前页的业务数据。
            message:   状态描述。
            total:     总记录数。
            page:      当前页码。
            page_size: 每页记录数。
            has_more:  是否还有更多数据。

        Returns:
            ``PaginationResult`` 实例。
        """
        return PaginationResult[T](
            code=0,
            message=message,
            data=data,
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# 快捷辅助函数
# ---------------------------------------------------------------------------

def success(
    data: Any = None,
    message: str = "success",
) -> Result[Any]:
    """快捷创建成功响应。

    Args:
        data:    业务数据。
        message: 状态描述。

    Returns:
        ``Result`` 成功实例。
    """
    return Result.success(data=data, message=message)


def error(
    code: int,
    message: str,
) -> Result[Any]:
    """快捷创建错误响应。

    Args:
        code:    错误码（非零）。
        message: 错误描述。

    Returns:
        ``Result`` 错误实例。
    """
    return Result.error(code=code, message=message)
