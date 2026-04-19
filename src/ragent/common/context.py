"""
用户上下文模块 —— 基于 ContextVar 的协程安全用户上下文传播

通过 ``ContextVar`` 在异步协程之间安全地传递当前用户信息，
避免显式地在每一层函数签名中传递用户对象。

典型用法::

    from ragent.common.context import UserContext, UserContextManager

    # 在中间件或入口处设置上下文
    user = UserContext(user_id="u001", username="alice")
    async with UserContextManager(user):
        # 在任意深层调用中获取当前用户
        uid = get_current_user_id()
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 用户上下文数据类
# ---------------------------------------------------------------------------

@dataclass
class UserContext:
    """当前请求关联的用户信息。

    Attributes:
        user_id:   用户唯一标识。
        username:  用户名。
        role:      用户角色，默认 ``'user'``。
        tenant_id: 所属租户 ID，可选。
        extra:     扩展字段，用于携带额外的用户元数据。
    """

    user_id: str
    username: str
    role: str = "user"
    tenant_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ContextVar 定义
# ---------------------------------------------------------------------------

_user_context_var: ContextVar[UserContext | None] = ContextVar(
    "user_context",
    default=None,
)
"""协程安全的用户上下文变量，默认值为 ``None``。"""


# ---------------------------------------------------------------------------
# 上下文操作函数
# ---------------------------------------------------------------------------

def set_user_context(user: UserContext) -> None:
    """设置当前协程的用户上下文。

    Args:
        user: 要绑定的用户上下文实例。
    """
    _user_context_var.set(user)


def get_user_context() -> UserContext | None:
    """获取当前协程的用户上下文。

    Returns:
        若已设置则返回 ``UserContext``，否则返回 ``None``。
    """
    return _user_context_var.get()


def get_current_user_id() -> str | None:
    """获取当前协程的用户 ID。

    Returns:
        若已设置则返回 ``user_id``，否则返回 ``None``。
    """
    ctx = _user_context_var.get()
    return ctx.user_id if ctx is not None else None


def clear_user_context() -> None:
    """清除当前协程的用户上下文。"""
    _user_context_var.set(None)


# ---------------------------------------------------------------------------
# 异步上下文管理器
# ---------------------------------------------------------------------------

class UserContextManager:
    """用户上下文异步上下文管理器。

    在 ``async with`` 块入口处自动设置用户上下文，退出时自动清除，
    确保协程结束后不会泄漏上下文信息。

    用法::

        user = UserContext(user_id="u001", username="alice")
        async with UserContextManager(user):
            ...
    """

    def __init__(self, user: UserContext) -> None:
        self._user: UserContext = user

    async def __aenter__(self) -> UserContextManager:
        """进入上下文时设置用户信息。"""
        set_user_context(self._user)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        """退出上下文时清除用户信息。"""
        clear_user_context()
