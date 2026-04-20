"""
用户认证路由模块。

提供以下端点：
    - ``POST /api/v1/auth/register`` —— 用户注册
    - ``POST /api/v1/auth/login``    —— 用户登录（返回 JWT）
    - ``GET  /api/v1/auth/me``       —— 获取当前用户信息（需认证）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ragent.app.deps import CurrentUser, DbSession
from ragent.common.logging import get_logger
from ragent.common.models import User
from ragent.common.response import Result
from ragent.common.snowflake import generate_id
from ragent.infra.auth import create_access_token, hash_password, verify_password

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """用户注册请求体。"""

    username: str = Field(..., min_length=3, max_length=32, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")
    department_id: int | None = Field(default=None, description="部门 ID")


class LoginRequest(BaseModel):
    """用户登录请求体。"""

    username: str = Field(..., min_length=1, description="用户名")
    password: str = Field(..., min_length=1, description="密码")


class UserResponse(BaseModel):
    """用户信息响应。"""

    id: int
    username: str
    role: str
    avatar: str | None = None


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

@router.post("/register", summary="用户注册")
async def register(
    request: RegisterRequest,
    db: DbSession,
) -> Result[Any]:
    """注册新用户。

    流程：
        1. 检查用户名是否已存在
        2. 生成 Snowflake ID
        3. 哈希密码并写入数据库

    Args:
        request: 注册请求体（username + password）。
        db: 异步数据库会话。

    Returns:
        Result 包含新用户信息和 JWT Token。
    """
    # 检查用户名是否已存在
    result = await db.execute(
        select(User).where(User.username == request.username)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用户名 '{request.username}' 已被注册",
        )

    # 生成 ID + 哈希密码
    user_id = generate_id()
    password_hash = hash_password(request.password)

    # 创建用户
    user = User(
        id=user_id,
        username=request.username,
        password_hash=password_hash,
        role="user",
        department_id=request.department_id,
    )
    db.add(user)
    await db.flush()

    # 生成 JWT（sub 必须为字符串）
    token = create_access_token(data={"sub": str(user.id)})

    logger.info("用户注册成功: id=%s, username=%s", user.id, user.username)

    return Result.success(data={
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "department_id": user.department_id,
        },
        "access_token": token,
        "token_type": "bearer",
    })


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------

@router.post("/login", summary="用户登录")
async def login(
    request: LoginRequest,
    db: DbSession,
) -> Result[Any]:
    """用户登录，返回 JWT Token。

    流程：
        1. 按用户名查找用户
        2. 验证密码
        3. 生成 JWT 并返回

    Args:
        request: 登录请求体（username + password）。
        db: 异步数据库会话。

    Returns:
        Result 包含用户信息和 JWT Token。
    """
    # 查找用户
    result = await db.execute(
        select(User).where(User.username == request.username)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    # 生成 JWT（sub 必须为字符串）
    token = create_access_token(data={"sub": str(user.id)})

    logger.info("用户登录成功: id=%s, username=%s", user.id, user.username)

    return Result.success(data={
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "avatar": user.avatar,
            "department_id": user.department_id,
        },
        "access_token": token,
        "token_type": "bearer",
    })


# ---------------------------------------------------------------------------
# 当前用户信息
# ---------------------------------------------------------------------------

@router.get("/me", summary="获取当前用户信息")
async def get_me(
    current_user: CurrentUser,
) -> Result[Any]:
    """获取当前登录用户的信息（需 Bearer Token）。

    Args:
        current_user: 通过 JWT 解析注入的当前用户。

    Returns:
        Result 包含用户信息。
    """
    return Result.success(data={
        "id": current_user.id,
        "username": current_user.username,
        "role": current_user.role,
        "avatar": current_user.avatar,
        "department_id": current_user.department_id,
    })
