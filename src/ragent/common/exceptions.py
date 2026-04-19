"""
异常模块 —— 三层异常体系

为 ragent 项目提供统一的异常层次结构：
    BaseError                所有自定义异常的基类
    ├── ClientException      4xx 客户端错误（前缀 A）
    ├── ServiceException     5xx 服务端错误（前缀 B）
    └── RemoteException      远程调用错误（前缀 C）
"""

from __future__ import annotations

from typing import NoReturn


# ---------------------------------------------------------------------------
# 基础异常
# ---------------------------------------------------------------------------

class BaseError(Exception):
    """所有自定义异常的基类。

    Attributes:
        error_code: 错误编码，例如 ``A1001``、``B2001``、``C3001``。
        message:    人类可读的错误描述。
    """

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code: str = error_code
        self.message: str = message
        super().__init__(self.error_code, self.message)

    def __str__(self) -> str:
        """返回 ``[error_code] message`` 格式的字符串。"""
        return f"[{self.error_code}] {self.message}"

    def __repr__(self) -> str:
        """返回便于调试的表示形式。"""
        return f"{self.__class__.__name__}(error_code={self.error_code!r}, message={self.message!r})"

    def to_dict(self) -> dict[str, str]:
        """将异常信息转换为字典。"""
        return {
            "error_code": self.error_code,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# 客户端异常（4xx）
# ---------------------------------------------------------------------------

class ClientException(BaseError):
    """客户端错误（HTTP 4xx）。

    错误编码以 ``A`` 为前缀。

    预定义示例：
        - A1001 参数校验失败
        - A1002 权限不足
        - A1003 资源不存在
    """

    http_status: int = 400

    def __init__(
        self,
        error_code: str = "A1000",
        message: str = "客户端错误",
    ) -> None:
        super().__init__(error_code=error_code, message=message)


# ---------------------------------------------------------------------------
# 服务端异常（5xx）
# ---------------------------------------------------------------------------

class ServiceException(BaseError):
    """服务端错误（HTTP 5xx）。

    错误编码以 ``B`` 为前缀。

    预定义示例：
        - B2001 业务逻辑异常
        - B2002 数据不一致
        - B2003 知识库不存在
    """

    http_status: int = 500

    def __init__(
        self,
        error_code: str = "B2000",
        message: str = "服务端错误",
    ) -> None:
        super().__init__(error_code=error_code, message=message)


# ---------------------------------------------------------------------------
# 远程调用异常
# ---------------------------------------------------------------------------

class RemoteException(BaseError):
    """远程调用错误。

    错误编码以 ``C`` 为前缀。

    预定义示例：
        - C3001 模型服务不可用
        - C3002 网络超时
        - C3003 向量数据库异常
    """

    http_status: int = 502

    def __init__(
        self,
        error_code: str = "C3000",
        message: str = "远程调用错误",
    ) -> None:
        super().__init__(error_code=error_code, message=message)


# ---------------------------------------------------------------------------
# 便捷工厂函数
# ---------------------------------------------------------------------------

def raise_client_error(code: str, msg: str) -> NoReturn:
    """抛出客户端异常的便捷函数。

    Args:
        code: 错误编码（建议以 ``A`` 开头）。
        msg:  错误描述信息。
    """
    raise ClientException(error_code=code, message=msg)


def raise_service_error(code: str, msg: str) -> NoReturn:
    """抛出服务端异常的便捷函数。

    Args:
        code: 错误编码（建议以 ``B`` 开头）。
        msg:  错误描述信息。
    """
    raise ServiceException(error_code=code, message=msg)


def raise_remote_error(code: str, msg: str) -> NoReturn:
    """抛出远程调用异常的便捷函数。

    Args:
        code: 错误编码（建议以 ``C`` 开头）。
        msg:  错误描述信息。
    """
    raise RemoteException(error_code=code, message=msg)
