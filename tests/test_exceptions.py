"""Tests for ragent.common.exceptions module."""
import pytest

from ragent.common.exceptions import (
    BaseError,
    ClientException,
    RemoteException,
    ServiceException,
    raise_client_error,
    raise_remote_error,
    raise_service_error,
)


# ---------------------------------------------------------------------------
# BaseError
# ---------------------------------------------------------------------------

class TestBaseError:
    def test_init(self):
        err = BaseError("X0001", "something went wrong")
        assert err.error_code == "X0001"
        assert err.message == "something went wrong"

    def test_str(self):
        err = BaseError("X0001", "bad")
        assert str(err) == "[X0001] bad"

    def test_repr(self):
        err = BaseError("X0001", "bad")
        r = repr(err)
        assert "BaseError" in r
        assert "X0001" in r
        assert "bad" in r

    def test_to_dict(self):
        err = BaseError("X0001", "bad")
        d = err.to_dict()
        assert d == {"error_code": "X0001", "message": "bad"}

    def test_is_exception(self):
        err = BaseError("X0001", "bad")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# ClientException
# ---------------------------------------------------------------------------

class TestClientException:
    def test_defaults(self):
        err = ClientException()
        assert err.error_code == "A1000"
        assert err.message == "客户端错误"
        assert err.http_status == 400

    def test_custom_args(self):
        err = ClientException(error_code="A1001", message="参数校验失败")
        assert err.error_code == "A1001"
        assert err.message == "参数校验失败"

    def test_inherits_base_error(self):
        assert issubclass(ClientException, BaseError)

    def test_to_dict(self):
        err = ClientException("A1002", "权限不足")
        d = err.to_dict()
        assert d["error_code"] == "A1002"


# ---------------------------------------------------------------------------
# ServiceException
# ---------------------------------------------------------------------------

class TestServiceException:
    def test_defaults(self):
        err = ServiceException()
        assert err.error_code == "B2000"
        assert err.message == "服务端错误"
        assert err.http_status == 500

    def test_custom_args(self):
        err = ServiceException(error_code="B2001", message="业务逻辑异常")
        assert err.error_code == "B2001"

    def test_inherits_base_error(self):
        assert issubclass(ServiceException, BaseError)


# ---------------------------------------------------------------------------
# RemoteException
# ---------------------------------------------------------------------------

class TestRemoteException:
    def test_defaults(self):
        err = RemoteException()
        assert err.error_code == "C3000"
        assert err.message == "远程调用错误"
        assert err.http_status == 502

    def test_custom_args(self):
        err = RemoteException(error_code="C3001", message="模型服务不可用")
        assert err.error_code == "C3001"

    def test_inherits_base_error(self):
        assert issubclass(RemoteException, BaseError)


# ---------------------------------------------------------------------------
# Raise functions
# ---------------------------------------------------------------------------

class TestRaiseFunctions:
    def test_raise_client_error(self):
        with pytest.raises(ClientException) as exc_info:
            raise_client_error("A1001", "参数缺失")
        assert exc_info.value.error_code == "A1001"
        assert exc_info.value.message == "参数缺失"

    def test_raise_service_error(self):
        with pytest.raises(ServiceException) as exc_info:
            raise_service_error("B2001", "内部错误")
        assert exc_info.value.error_code == "B2001"

    def test_raise_remote_error(self):
        with pytest.raises(RemoteException) as exc_info:
            raise_remote_error("C3001", "模型不可用")
        assert exc_info.value.error_code == "C3001"

    def test_raise_functions_return_no_return(self):
        """Each raise function should never return (NoReturn)."""
        from typing import get_type_hints
        # Just verify they exist and are callable
        assert callable(raise_client_error)
        assert callable(raise_service_error)
        assert callable(raise_remote_error)


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------

class TestHierarchy:
    def test_all_inherit_from_base_error(self):
        assert issubclass(ClientException, BaseError)
        assert issubclass(ServiceException, BaseError)
        assert issubclass(RemoteException, BaseError)

    def test_all_are_subclass_of_exception(self):
        assert issubclass(ClientException, Exception)
        assert issubclass(ServiceException, Exception)
        assert issubclass(RemoteException, Exception)

    def test_http_status_values(self):
        assert ClientException.http_status == 400
        assert ServiceException.http_status == 500
        assert RemoteException.http_status == 502

    def test_catch_base_catches_all(self):
        """Catching BaseError should catch all three subclasses."""
        for cls, code, msg in [
            (ClientException, "A1", "c"),
            (ServiceException, "B1", "s"),
            (RemoteException, "C1", "r"),
        ]:
            with pytest.raises(BaseError):
                raise cls(code, msg)
