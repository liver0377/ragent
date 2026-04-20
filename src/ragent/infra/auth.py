"""
JWT 认证与密码哈希工具模块。

提供：
    - 密码哈希与验证（passlib bcrypt）
    - JWT Token 生成与解析（PyJWT）
"""

from __future__ import annotations

import datetime
from typing import Any

import jwt
from passlib.context import CryptContext

from ragent.config.settings import get_settings

# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """对明文密码进行 bcrypt 哈希。

    Args:
        password: 明文密码。

    Returns:
        哈希后的密码字符串。
    """
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配。

    Args:
        plain_password: 用户输入的明文密码。
        hashed_password: 数据库中存储的哈希密码。

    Returns:
        True 表示密码正确。
    """
    return _pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# JWT Token
# ---------------------------------------------------------------------------

def create_access_token(
    data: dict[str, Any],
    expires_delta: datetime.timedelta | None = None,
) -> str:
    """生成 JWT Access Token。

    Args:
        data: 要编码到 token 中的载荷数据（通常包含 sub=用户ID）。
        expires_delta: 自定义过期时间间隔。None 时使用配置中的默认值。

    Returns:
        编码后的 JWT 字符串。
    """
    settings = get_settings()

    to_encode = data.copy()
    now = datetime.datetime.now(datetime.timezone.utc)

    if expires_delta is None:
        expires_delta = datetime.timedelta(
            minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        )

    to_encode.update({
        "iat": now,
        "exp": now + expires_delta,
    })

    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """解码并验证 JWT Token。

    Args:
        token: JWT 字符串。

    Returns:
        Token 载荷字典。

    Raises:
        jwt.ExpiredSignatureError: Token 已过期。
        jwt.InvalidTokenError: Token 无效。
    """
    settings = get_settings()
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
