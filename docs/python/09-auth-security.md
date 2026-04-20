# Ragent Python 版 — JWT 认证与部门权限系统

## 1. 概述

Ragent 的认证与安全体系围绕 **JWT 无状态令牌** 和 **部门级权限隔离** 两大核心机制构建：

- **JWT 认证**：基于 PyJWT + bcrypt 的无状态认证方案，注册/登录后签发 Token，后续请求通过 Bearer Token 鉴权
- **部门权限隔离**：User 和 KnowledgeBase 均关联 Department，普通用户只能访问本部门或无部门归属的知识库，管理员拥有全局访问权限
- **IP 限流**：基于 Redis 滑动窗口的 IP 级别速率限制，防止注册刷号、登录暴力破解等滥用行为
- **Snowflake ID**：所有用户 ID 使用 Snowflake 分布式 ID 生成器，避免自增 ID 暴露业务信息

**技术选型：**

- **PyJWT** — JWT Token 的签发与验证
- **passlib (bcrypt)** — 密码哈希与验证
- **SQLAlchemy 2.0 async + asyncpg** — 异步数据库访问
- **Redis sorted set** — 滑动窗口限流

---

## 2. 模块架构

```
┌─────────────────────────────────────────────────────┐
│                   auth_router.py                     │
│           POST /register, POST /login, GET /me       │
│              (认证端点，注册/登录/当前用户)            │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────┼────────────────┐
          ▼            ▼                ▼
   ┌────────────┐ ┌──────────┐  ┌──────────────────┐
   │  deps.py   │ │ auth.py  │  │  rate_limit.py   │
   │ CurrentUser│ │ JWT签发  │  │  限流中间件       │
   │ DbSession  │ │ 密码哈希 │  │  Redis滑动窗口   │
   └──────┬─────┘ └────┬─────┘  └──────────────────┘
          │            │
          ▼            ▼
   ┌──────────┐  ┌────────────┐
   │ models.py│  │ settings.py│
   │ User     │  │ JWT_SECRET │
   │ Department│ │ JWT_ALGO   │
   └──────────┘  └────────────┘
```

**源码文件分布：**

- **`src/ragent/app/auth_router.py`** — 认证路由（register / login / me 三个端点）
- **`src/ragent/app/deps.py`** — FastAPI 依赖注入（CurrentUser / DbSession 类型别名）
- **`src/ragent/infra/auth.py`** — JWT 签发/验证 + bcrypt 密码哈希
- **`src/ragent/common/models.py`** — User / Department ORM 模型
- **`src/ragent/config/settings.py`** — JWT 配置项（密钥 / 算法 / 过期时间）
- **`src/ragent/app/rate_limit.py`** — IP 级别限流中间件
- **`src/ragent/app/router.py`** — 部门权限检查函数 `_check_kb_dept_access`

---

## 3. JWT 认证流程

### 3.1 Token 签发

JWT Token 的签发由 `infra/auth.py` 中的 `create_access_token()` 函数完成：

```
用户注册/登录
  │
  ▼
create_access_token(data={"sub": str(user_id)})
  │
  ├── 读取 Settings 中的 JWT_SECRET_KEY / JWT_ALGORITHM
  ├── 设置 iat（签发时间）= 当前 UTC 时间
  ├── 设置 exp（过期时间）= iat + JWT_ACCESS_TOKEN_EXPIRE_MINUTES
  └── jwt.encode() → 返回 JWT 字符串
```

**Token 载荷（Payload）结构：**

- **`sub`** — 用户 ID（字符串形式，如 `"1234567890"`）
- **`iat`** — 签发时间（UTC 时间戳）
- **`exp`** — 过期时间（UTC 时间戳）

### 3.2 Token 验证

Token 验证由 `app/deps.py` 中的 `get_current_user()` 依赖函数执行，流程如下：

```
请求到达（携带 Authorization: Bearer <token>）
  │
  ▼
HTTPBearer(auto_error=False) 提取 credentials
  │
  ├── credentials 为 None → 401 "未提供认证凭据"
  │
  ▼
decode_access_token(token) 解码 JWT
  │
  ├── ExpiredSignatureError → 401 "Token 已过期，请重新登录"
  ├── InvalidTokenError → 401 "Token 无效"
  │
  ▼
提取 payload["sub"] 作为用户 ID
  │
  ├── sub 缺失 → 401 "Token 缺少用户标识"
  ├── sub 非数字 → 401 "Token 中用户标识格式错误"
  │
  ▼
数据库查询 User（select where id == user_id）
  │
  ├── 用户不存在 → 401 "用户不存在"
  │
  ▼
返回 User ORM 对象 → 注入到路由函数
```

**关键设计点：**

- **`HTTPBearer(auto_error=False)`** — 不自动抛异常，而是返回 `None`，由 `get_current_user()` 自行处理，以返回自定义错误消息
- **所有 401 响应都携带 `WWW-Authenticate: Bearer` 头**，符合 HTTP 规范
- **Token 解码后仍然查询数据库**，确保已删除/禁用的用户即使持有有效 Token 也无法访问

### 3.3 JWT 配置项

配置通过 `config/settings.py` 管理，支持环境变量覆盖：

- **`JWT_SECRET_KEY`** — HMAC 签名密钥，默认值 `ragent-jwt-secret-change-in-production-2026`（生产环境务必通过环境变量更换）
- **`JWT_ALGORITHM`** — 签名算法，默认 `HS256`
- **`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`** — Token 有效期，默认 `1440` 分钟（24 小时）

配置优先级（从高到低）：系统环境变量 → `.env` 文件 → 字段默认值。

---

## 4. 密码处理

密码安全由 `infra/auth.py` 中的 passlib `CryptContext` 承担：

```
passlib CryptContext(schemes=["bcrypt"], deprecated="auto")
```

**两个核心函数：**

- **`hash_password(password: str) -> str`** — 将明文密码通过 bcrypt 算法哈希，返回哈希字符串存入数据库
- **`verify_password(plain_password: str, hashed_password: str) -> bool`** — 将用户输入的明文密码与数据库中的哈希比对

**安全特性：**

- **bcrypt 自带盐值（salt）**，每次哈希结果不同，防止彩虹表攻击
- **bcrypt 自适应 cost factor**，计算复杂度可随硬件性能提升而调整
- **数据库仅存储 `password_hash`**，永远不存储明文密码

---

## 5. 认证端点详解

### 5.1 用户注册 — POST /api/v1/auth/register

**请求体（RegisterRequest）：**

- **`username`** — 用户名，3~32 个字符，必填
- **`password`** — 密码，6~128 个字符，必填
- **`department_id`** — 部门 ID，可选，默认 `None`

**注册流程：**

1. **唯一性检查** — 查询数据库 `SELECT FROM t_user WHERE username = ?`，若已存在则返回 `409 Conflict`（`用户名 'xxx' 已被注册`）
2. **生成 Snowflake ID** — 调用 `generate_id()` 生成 BigInteger 主键
3. **密码哈希** — 调用 `hash_password()` 对明文密码做 bcrypt 哈希
4. **创建用户** — 构造 User 对象，`role` 默认为 `"user"`，写入数据库（`flush`）
5. **签发 JWT** — 调用 `create_access_token(data={"sub": str(user_id)})`
6. **返回结果** — 包含用户信息和 `access_token`

**注册成功响应示例：**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "user": {
      "id": 1234567890123456,
      "username": "zhangsan",
      "role": "user",
      "department_id": 100
    },
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer"
  }
}
```

### 5.2 用户登录 — POST /api/v1/auth/login

**请求体（LoginRequest）：**

- **`username`** — 用户名，至少 1 个字符，必填
- **`password`** — 密码，至少 1 个字符，必填

**登录流程：**

1. **查找用户** — 查询数据库 `SELECT FROM t_user WHERE username = ?`
2. **验证密码** — 调用 `verify_password(password, user.password_hash)`
   - 用户不存在 **或** 密码错误均返回 `401 Unauthorized`（`用户名或密码错误`）
   - **不区分"用户名不存在"和"密码错误"**，防止用户名枚举攻击
3. **签发 JWT** — 调用 `create_access_token(data={"sub": str(user.id)})`
4. **返回结果** — 包含用户信息和 `access_token`

**登录成功响应包含的额外字段：**

- **`avatar`** — 用户头像 URL

### 5.3 获取当前用户 — GET /api/v1/auth/me

**认证要求：** 必须携带 `Authorization: Bearer <token>` 请求头

**依赖注入链路：**

```
请求 → HTTPBearer 提取 Token → decode_access_token 解码
     → 提取 sub（user_id） → 查询数据库 → 返回 User 对象
```

**返回字段：**

- `id`、`username`、`role`、`avatar`、`department_id`

---

## 6. 依赖注入机制

### 6.1 类型别名定义

`app/deps.py` 定义了两个全局类型别名，供路由函数通过类型注解声明依赖：

- **`CurrentUser`** — `Annotated[User, Depends(get_current_user)]`，JWT 认证后的当前用户 ORM 对象
- **`DbSession`** — `Annotated[AsyncSession, Depends(get_db)]`，异步数据库会话

### 6.2 使用方式

在路由函数参数中直接使用类型注解即可触发依赖注入：

```python
async def my_route(
    current_user: CurrentUser,   # 自动提取 Bearer Token → 验证 → 查DB → 注入User
    db: DbSession,               # 自动创建异步数据库会话
):
    ...
```

**优势：**

- 路由函数无需关心 Token 提取、解码、数据库查询等细节
- 依赖项可复用，所有需要认证的端点统一使用 `CurrentUser`
- FastAPI 自动生成 OpenAPI 文档中的安全要求标注

---

## 7. 部门权限隔离

### 7.1 数据模型关系

```
┌──────────────────┐       ┌──────────────────┐
│   t_department   │       │     t_user        │
│                  │       │                   │
│ id (BigInteger)  │◄──┐   │ id (BigInteger)   │
│ name (unique)    │   │   │ username (unique) │
│ description      │   │   │ password_hash     │
└──────┬───────────┘   │   │ role              │
       │               │   │ department_id ────┘ (FK)
       │               │   │ avatar            │
       │               │   └───────────────────┘
       │               │
       │   ┌───────────┘
       ▼   ▼
┌──────────────────────┐
│  t_knowledge_base    │
│                      │
│ id (BigInteger)      │
│ name                 │
│ department_id (FK)   │──► t_department.id
│ embedding_model      │
│ collection_name      │
└──────────────────────┘
```

**核心关系：**

- User 通过 `department_id` 外键关联 Department（可为 NULL，表示未分配部门）
- KnowledgeBase 通过 `department_id` 外键关联 Department（可为 NULL，表示公共知识库）
- Department 与 User 是一对多关系，与 KnowledgeBase 也是一对多关系

### 7.2 权限检查函数

部门权限检查由 `app/router.py` 中的 `_check_kb_dept_access()` 函数实现：

```python
def _check_kb_dept_access(kb: KnowledgeBase, user) -> str | None:
    """检查用户是否有权访问该知识库。"""
    if user.role == "admin":
        return None                          # 管理员：可访问所有知识库
    if kb.department_id is None:
        return None                          # 公共知识库：所有人可访问
    if kb.department_id != user.department_id:
        return f"无权访问知识库 {kb.id}（部门隔离）"  # 跨部门：拒绝
    return None                              # 本部门：允许
```

**权限规则总结：**

- **管理员（role == "admin"）** — 可访问所有知识库，无部门限制
- **公共知识库（department_id == None）** — 所有用户均可访问
- **部门知识库** — 仅 `user.department_id == kb.department_id` 的用户可访问
- **跨部门访问** — 返回错误消息，HTTP 403

### 7.3 调用场景

`_check_kb_dept_access` 在以下知识库操作中被调用：

- **查看知识库详情** — 确认用户有权查看该知识库
- **更新知识库** — 确认用户有权修改该知识库
- **删除知识库** — 确认用户有权删除该知识库
- **上传文档到知识库** — 确认用户有权向该知识库上传文档
- **文档入库操作** — 确认用户有权操作该知识库的文档

---

## 8. 限流机制

### 8.1 限流中间件

限流由 `app/rate_limit.py` 中的 `RateLimitMiddleware` 实现，基于 Redis sorted set 的**滑动窗口算法**：

```
请求到达
  │
  ▼
匹配限流规则（路径前缀 + HTTP 方法）
  │
  ├── 无匹配规则 → 直接放行
  │
  ▼
获取客户端 IP（支持 X-Forwarded-For / X-Real-IP）
  │
  ▼
Redis 滑动窗口检查：
  ├── ZREMRANGEBYSCORE — 移除窗口外的旧记录
  ├── ZADD — 添加当前请求（score=时间戳, member=时间戳:随机ID）
  ├── ZCARD — 统计窗口内请求数
  └── EXPIRE — 设置 key 过期时间（兜底清理）
  │
  ├── count <= max_requests → 放行
  └── count > max_requests → 429 Too Many Requests
```

### 8.2 认证相关限流规则

**注册端点 — POST /api/v1/auth/register：**

- 限流阈值：**5 次 / 60 秒**（按 IP）
- 防御目标：防止恶意批量注册、刷号

**登录端点 — POST /api/v1/auth/login：**

- 限流阈值：**10 次 / 60 秒**（按 IP）
- 防御目标：防止暴力破解密码

### 8.3 其他限流规则

- **聊天端点 — POST /api/v1/chat**：20 次 / 60 秒，防止 API 滥用
- **文档上传端点 — POST /api/v1/documents/upload**：10 次 / 60 秒，防止大量文件上传

### 8.4 限流 Key 格式

Redis Key 格式：`ratelimit:{ip}:{path_prefix}`

例如：`ratelimit:192.168.1.100:/api/v1/auth/register`

### 8.5 容错设计

当 Redis 不可用时，限流中间件**不会阻塞请求**，而是打印警告日志并放行：

```python
except Exception:
    logger.warning("Redis 不可用，跳过限流检查")
    return True  # 放行
```

这确保了 Redis 故障不会导致整个服务不可用。

---

## 9. Snowflake ID 与用户标识

所有 User 的主键 `id` 使用 Snowflake 分布式 ID 生成器产生：

- **类型** — `BigInteger`（64 位整数）
- **生成方式** — `generate_id()` 函数，Snowflake 算法保证全局唯一且时间有序
- **JWT 中的表示** — Token 载荷的 `sub` 字段为 `str(user_id)`，解码后通过 `int()` 转回整数
- **优势** — 不暴露注册顺序、不依赖数据库自增、分布式环境下无冲突

---

## 10. 安全设计总结

**认证安全：**

- 密码使用 bcrypt 哈希存储，自带随机盐值，防止彩虹表攻击
- 登录失败不区分"用户不存在"和"密码错误"，防止用户名枚举
- JWT 签名使用 HS256 + 密钥，生产环境必须更换强密钥
- Token 有效期 24 小时，过期后需要重新登录
- 每次请求都查询数据库验证用户存在性，支持即时吊销

**限流安全：**

- 注册 5 次/分钟，登录 10 次/分钟，防止暴力攻击
- 基于 IP 的滑动窗口算法，精度优于固定窗口
- 支持 Nginx 反向代理场景（X-Forwarded-For / X-Real-IP）
- Redis 故障时优雅降级，不阻塞正常请求

**权限安全：**

- 管理员/普通用户角色分离（`role` 字段）
- 部门级知识库隔离，普通用户只能访问本部门或公共知识库
- 权限检查在每次知识库操作前执行，无绕过可能

---

## 11. 相关文档

- [01-overview.md](01-overview.md) — 项目总览、技术栈、模块分层
- [02-framework.md](02-framework.md) — 框架层：FastAPI 路由 / 依赖注入 / 中间件
- [07-data-model.md](07-data-model.md) — 数据模型：PostgreSQL 表结构 / Snowflake ID
- [11-api-reference.md](11-api-reference.md) — API 参考：全部接口端点
