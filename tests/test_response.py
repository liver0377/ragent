"""Tests for ragent.common.response module."""
import time

import pytest

from ragent.common.exceptions import (
    BaseError,
    ClientException,
    RemoteException,
    ServiceException,
)
from ragent.common.response import (
    PaginationResult,
    Result,
    error,
    success,
)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

class TestResult:
    def test_default_values(self):
        r = Result()
        assert r.code == 0
        assert r.message == "success"
        assert r.data is None
        assert r.trace_id is None
        assert r.timestamp > 0

    def test_static_success(self):
        r = Result.success(data={"id": 1, "name": "test"})
        assert r.code == 0
        assert r.message == "success"
        assert r.data == {"id": 1, "name": "test"}
        assert r.timestamp > 0

    def test_static_success_no_data(self):
        r = Result.success()
        assert r.code == 0
        assert r.data is None

    def test_static_success_custom_message(self):
        r = Result.success(data="hello", message="自定义成功")
        assert r.message == "自定义成功"

    def test_static_error(self):
        r = Result.error(code=1001, message="参数缺失")
        assert r.code == 1001
        assert r.message == "参数缺失"
        assert r.data is None

    def test_static_error_with_trace_id(self):
        r = Result.error(code=5000, message="internal", trace_id="abc123")
        assert r.trace_id == "abc123"

    def test_from_exception_client(self):
        exc = ClientException(error_code="A1001", message="参数校验失败")
        r = Result.from_exception(exc)
        assert r.code != 0  # non-zero error code
        assert r.message == "参数校验失败"
        assert r.data is None

    def test_from_exception_service(self):
        exc = ServiceException(error_code="B2001", message="内部错误")
        r = Result.from_exception(exc)
        assert r.code != 0
        assert r.message == "内部错误"

    def test_from_exception_remote(self):
        exc = RemoteException(error_code="C3001", message="服务不可用")
        r = Result.from_exception(exc)
        assert r.code != 0

    def test_from_exception_numeric_code_is_consistent(self):
        """Same error_code should produce same numeric code."""
        exc = ClientException(error_code="A1001", message="test")
        r1 = Result.from_exception(exc)
        r2 = Result.from_exception(exc)
        assert r1.code == r2.code


# ---------------------------------------------------------------------------
# PaginationResult
# ---------------------------------------------------------------------------

class TestPaginationResult:
    def test_defaults(self):
        r = PaginationResult()
        assert r.total == 0
        assert r.page == 1
        assert r.page_size == 20
        assert r.has_more is False

    def test_static_success(self):
        items = [{"id": 1}, {"id": 2}]
        r = PaginationResult.success(
            data=items,
            total=100,
            page=2,
            page_size=10,
            has_more=True,
        )
        assert r.code == 0
        assert r.data == items
        assert r.total == 100
        assert r.page == 2
        assert r.page_size == 10
        assert r.has_more is True

    def test_static_success_custom_message(self):
        r = PaginationResult.success(data=[], message="获取成功")
        assert r.message == "获取成功"

    def test_inherits_result(self):
        assert issubclass(PaginationResult, Result)


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    def test_success_helper(self):
        r = success(data={"items": []})
        assert isinstance(r, Result)
        assert r.code == 0
        assert r.data == {"items": []}

    def test_success_helper_default(self):
        r = success()
        assert r.code == 0
        assert r.data is None

    def test_error_helper(self):
        r = error(code=1001, message="参数缺失")
        assert isinstance(r, Result)
        assert r.code == 1001
        assert r.message == "参数缺失"
        assert r.data is None
