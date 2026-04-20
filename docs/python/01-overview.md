# Ragent Python 版 — 项目总览

## 1. 项目定位

Ragent 是一个 **RAG（检索增强生成）智能问答平台**，提供知识库管理、文档入库、智能对话等核心能力。

Python 版采用现代异步架构进行全面构建：

- **后端**：Python（FastAPI + Celery），异步生态，高性能
- **前端**：React SPA，通过 Nginx 反向代理对接后端 API
- **任务调度**：Celery + Redis，轻量可靠
- **向量存储**：pgvector（PostgreSQL 原生扩展），运维简单
- **认证体系**：JWT（PyJWT）+ bcrypt，无状态 Token
- **核心能力**：RAG 问答链路、文档入库流水线、部门级权限隔离、SSE 流式推送、分布式限流

---

## 2. 技术栈

- **语言与运行时** — Python 3.12+
- **Web 框架** — FastAPI + Uvicorn（ASGI）
- **数据验证** — Pydantic v2
- **关系数据库** — PostgreSQL + pgvector 扩展（向量存储）
- **ORM** — SQLAlchemy 2.0（async） + asyncpg
- **缓存 / 消息** — Redis 7.x + redis-py（缓存 + Celery Broker）
- **任务队列** — Celery 5.x + Redis（异步文档入库）
- **LLM 大模型** — GLM（智谱AI），model=`openai/glm-4-flash`
- **Embedding 模型** — SiliconFlow（硅基流动），model=`Qwen/Qwen3-Embedding-8B`
- **认证** — JWT（PyJWT） + bcrypt（passlib）
- **前端** — React 18 + TypeScript + Vite
- **反向代理** — Nginx（100MB 上传限制）
- **监控** — Prometheus + prometheus-client
- **可视化** — Grafana
- **容器化** — Docker Compose（7 个服务）

---

## 3. 模块分层图

```
┌──────────────────────────────────────────────────────────────┐
│                      Nginx 反向代理                           │
│                   (100MB 上传限制 / 静态资源)                 │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
       ┌───────▼───────┐               ┌───────▼───────┐
       │  React SPA    │               │  FastAPI API  │
       │  (frontend/)  │               │  (app/)       │
       │  React 18     │               │  Router / DI  │
       │  TypeScript   │               │  Middleware   │
       │  Vite         │               │  SSE Stream   │
       └───────────────┘               └──┬─────────┬──┘
                                         │         │
                          ┌──────────────▼──┐ ┌────▼──────────────┐
                          │  RAG 核心        │ │  文档入库管线      │
                          │  (rag/)          │ │  (ingestion/)     │
                          │  RAGChain        │ │  Celery Tasks     │
                          │  检索/生成       │ │  Pipeline Engine  │
                          └────────┬────────┘ └────┬──────────────┘
                                   │               │
                    ┌──────────────▼───────────────▼──┐
                    │       AI 基础设施 (infra/ai/)    │
                    │  LLM Service / Embedding Service │
                    │  Model Selector / Models         │
                    └───────────────┬──────────────────┘
                                    │
              ┌─────────────────────▼──────────────────────┐
              │            基础设施层 (infra/)               │
              │  database.py / auth.py / redis.py          │
              └────────┬──────────────────────┬────────────┘
                       │                      │
          ┌────────────▼─────────┐  ┌────────▼─────────────┐
          │  通用工具 (common/)  │  │  配置层 (config/)     │
          │  Snowflake / SSE     │  │  settings.py          │
          │  SafeJSON / Response │  │  Pydantic Settings    │
          │  Logging / JSON      │  │                       │
          └──────────────────────┘  └───────────────────────┘
```

---

## 4. 模块依赖关系

```
                          ┌──────────────┐
                          │    app/      │   HTTP API 层
                          │  router.py   │   (Router / Auth / Middleware)
                          │  deps.py     │
                          │  middleware/  │
                          └──┬───────┬───┘
                             │       │
                 ┌───────────┘       └───────────┐
                 ▼                               ▼
          ┌───────────┐                   ┌─────────────┐
          │   rag/    │                   │ ingestion/  │
          │ chain.py  │                   │  tasks.py   │
          │           │                   │  nodes.py   │
          │           │                   │  pipeline.py│
          │           │                   │  context.py │
          └─────┬─────┘                   └──────┬──────┘
                │                                │
                └──────────┬─────────────────────┘
                           ▼
                  ┌─────────────────┐
                  │   infra/ai/     │   AI 基础设施
                  │ llm_service.py  │   LLM 调用 / Embedding
                  │ embed_service   │   模型选择 / 统一接口
                  │ model_selector  │
                  │ models.py       │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │   infra/        │   基础设施
                  │  database.py    │   DB 连接池 / Auth / Redis
                  │  auth.py        │
                  │  redis.py       │
                  └────────┬────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
     ┌────────────────┐       ┌────────────────┐
     │   common/      │       │   config/      │
     │ snowflake.py   │       │ settings.py    │
     │ safe_json.py   │       │                │
     │ response.py    │       │                │
     │ sse.py         │       │                │
     │ json_utils.py  │       │                │
     │ logging.py     │       │                │
     │ models.py      │       │                │
     └────────────────┘       └────────────────┘
```

**依赖原则：**

- **config** — 最底层，零业务依赖，提供 Pydantic Settings 配置
- **common** — 通用工具层，仅依赖 config，提供 Snowflake ID / SafeJSON / SSE 等
- **infra** — 基础设施层，依赖 common + config，封装 DB / Auth / Redis
- **infra/ai** — AI 层，依赖 infra，屏蔽 LLM / Embedding 供应商差异
- **rag / ingestion** — 业务核心，依赖 infra/ai + infra + common
- **app** — 顶层 API 层，依赖所有业务模块

---

## 5. 核心业务流程

### 5.1 智能问答流程

用户发起提问的完整链路：

```
用户提问（HTTP POST /chat）
  │
  ▼
┌──────────────────────────────────────────────┐
│ 1. JWT 认证                                  │
│    校验 Token → 解析 user_id / department    │
└─────────────────┬────────────────────────────┘
                  ▼
┌──────────────────────────────────────────────┐
│ 2. IP 限流（Redis 滑动窗口）                 │
│    chat: 20次/min / register: 5次/min        │
│    login: 10次/min / upload: 10次/min        │
└─────────────────┬────────────────────────────┘
                  ▼
┌──────────────────────────────────────────────┐
│ 3. RAGChain 处理                              │
│    问题 → Embedding → pgvector 检索          │
│    → 上下文组装 → Prompt 构建                 │
└─────────────────┬────────────────────────────┘
                  ▼
┌──────────────────────────────────────────────┐
│ 4. LLM 生成（GLM 流式调用）                  │
│    model: openai/glm-4-flash                 │
│    base_url: open.bigmodel.cn                │
└─────────────────┬────────────────────────────┘
                  ▼
┌──────────────────────────────────────────────┐
│ 5. SSE 流式响应                               │
│    FastAPI StreamingResponse → 逐 token 推送 │
│    前端 EventSource 实时渲染                  │
└─────────────────┬────────────────────────────┘
                  ▼
┌──────────────────────────────────────────────┐
│ 6. 后处理                                     │
│    消息持久化 / 会话记录更新                   │
└──────────────────────────────────────────────┘
```

### 5.2 文档入库流程

```
用户上传文档（POST /upload）
  │
  ▼
┌──────────────────────────────────────────┐
│ Nginx 接收（100MB 限制）                 │
│ → FastAPI 保存至共享卷 ragent-upload-data│
│ → 文件去重检查                           │
└─────────────────┬────────────────────────┘
                  ▼
┌──────────────────────────────────────────┐
│ Celery 异步任务分发                       │
│ Fetcher → Parser → Enhancer → Chunker   │
│ → Enricher → Indexer                    │
└─────────────────┬────────────────────────┘
                  ▼
┌──────────────────────────────────────────┐
│ 入库完成回写                              │
│ asyncpg 写回 chunk_count 至 PostgreSQL   │
└──────────────────────────────────────────┘
```

---

## 6. JWT 认证系统

系统通过 JWT（JSON Web Token）实现无状态用户认证，基于 PyJWT + bcrypt 构建。

### 6.1 认证端点

所有认证端点挂载在 `APIRouter(prefix="/api/v1/auth")` 下：

- **POST /api/v1/auth/register** — 用户注册
  - 请求体：`username`（3~32字符）+ `password`（6~128字符）+ `department_id`（可选）
  - 流程：检查用户名唯一 → 生成 Snowflake ID → bcrypt 哈希密码 → 写入数据库 → 返回 JWT
  - 响应：用户信息 + `access_token`（Bearer Token）

- **POST /api/v1/auth/login** — 用户登录
  - 请求体：`username` + `password`
  - 流程：查找用户 → bcrypt 验证密码 → 生成 JWT → 返回
  - 响应：用户信息（含 avatar、department_id）+ `access_token`（Bearer Token）

- **GET /api/v1/auth/me** — 获取当前用户信息（需认证）
  - 请求头：`Authorization: Bearer <token>`
  - 流程：JWT 解析 → 依赖注入 CurrentUser → 返回用户信息
  - 响应：用户 id / username / role / avatar / department_id

### 6.2 Token 机制

- **签名算法**：HS256
- **Token 载荷**：`sub`（用户 ID，字符串形式）、`exp`（过期时间）
- **有效期**：默认 1440 分钟（24 小时），可通过 `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` 配置
- **密钥**：通过 `JWT_SECRET_KEY` 配置（生产环境务必更换）

### 6.3 密码安全

- 使用 bcrypt 算法（passlib）进行密码哈希
- 注册时 `hash_password()` 哈希后存储
- 登录时 `verify_password()` 对比验证

---

## 7. Rate Limiting（速率限制）

系统通过 `RateLimitMiddleware` 实现 IP 级别的请求速率限制，基于 Redis 滑动窗口算法。

### 7.1 限流规则

默认 4 条限流规则，保护关键端点：

- **POST /api/v1/auth/register** — 5 次/分钟（防刷号注册）
- **POST /api/v1/auth/login** — 10 次/分钟（防暴力破解密码）
- **POST /api/v1/chat** — 20 次/分钟（防滥用聊天接口）
- **POST /api/v1/documents/upload** — 10 次/分钟（防大量上传）

### 7.2 实现机制

- **算法**：Redis Sorted Set 滑动窗口
  - Key 格式：`ratelimit:{ip}:{path_prefix}`
  - 每次请求记录 timestamp 为 score，自动清理窗口外旧记录
  - Pipeline 原子操作：`ZREMRANGEBYSCORE` → `ZADD` → `ZCARD` → `EXPIRE`
- **IP 获取**：支持反向代理头（`X-Forwarded-For` → `X-Real-IP` → `request.client.host`）
- **超限响应**：HTTP 429 Too Many Requests，附带 `Retry-After` 头
- **降级策略**：Redis 不可用时跳过限流检查，不阻塞请求

### 7.3 中间件位置

`RateLimitMiddleware` 在中间件链最外层注册，优先拦截超限请求：

```
RateLimitMiddleware（最外层，优先拦截）
  └→ ExceptionHandlerMiddleware
       └→ RequestContextMiddleware
            └→ TraceMiddleware
                 └→ 路由处理
```

---

## 8. BigInt 精度修复

### 8.1 问题背景

JavaScript `Number` 类型的安全整数范围为 -(2^53 - 1) ~ 2^53 - 1（即 -9007199254740991 ~ 9007199254740991）。
Snowflake ID 通常为 18~19 位数字，远超此范围。若 JSON 响应中直接返回数字类型，前端 `JSON.parse()` 后精度会丢失。

### 8.2 解决方案

系统通过两层组件协同解决：

- **LargeIntJSONEncoder**（`common/json_utils.py`）— 自定义 JSON 编码器
  - 递归遍历数据结构，将绝对值超过 2^53 - 1 的整数自动转为字符串
  - 前端接收到字符串类型 ID，直接用字符串传递，无需数值运算

- **SafeJSONResponse**（`common/safe_json.py`）— 自定义 JSON Response
  - 继承 `starlette.responses.JSONResponse`
  - 重写 `render()` 方法，使用 `LargeIntJSONEncoder` 序列化

### 8.3 全局生效

在 `create_app()` 工厂函数中，将 `SafeJSONResponse` 设置为 FastAPI 的 `default_response_class`：

```python
app = FastAPI(
    ...
    default_response_class=SafeJSONResponse,
)
```

所有 API 响应自动经过 BigInt 精度保护，无需逐个手动处理。

---

## 9. 前端页面列表

前端基于 React 18 + TypeScript + Vite 构建，采用 SPA（单页应用）架构。

### 9.1 页面组成

- **LoginPage** — 用户登录/注册页面
  - 用户名 + 密码表单
  - 登录后获取 JWT Token 并存储到本地
  - 支持注册新账号

- **ChatPage** — 智能对话页面（核心功能）
  - 基于知识库的 RAG 问答
  - SSE 流式响应实时渲染
  - 会话历史管理

- **KnowledgePage** — 知识库管理页面
  - 知识库列表 / 创建 / 删除
  - 知识库基本信息编辑

- **DocumentsPage** — 文档管理页面
  - 文档列表 / 查看入库状态
  - 文档删除 / 重新入库

- **UploadPage** — 文档上传页面
  - 文件选择与上传（支持多种格式）
  - 上传进度展示
  - Nginx 100MB 上传限制

---

## 10. 关键配置项

以下为认证、限流、JSON 精度相关的核心配置项（通过环境变量或 `.env` 文件设置）：

### 10.1 JWT 认证配置

- **JWT_SECRET_KEY** — JWT 签名密钥，默认 `ragent-jwt-secret-change-in-production-2026`（生产环境务必更换）
- **JWT_ALGORITHM** — JWT 签名算法，默认 `HS256`
- **JWT_ACCESS_TOKEN_EXPIRE_MINUTES** — Access Token 过期时间（分钟），默认 `1440`（24 小时）

### 10.2 限流配置

- **RATE_LIMIT_MAX_CONCURRENT** — 最大并发请求数，默认 `10`
- **RATE_LIMIT_WINDOW_SECONDS** — 限流窗口秒数，默认 `60`

### 10.3 其他基础设施配置

- **REDIS_URL** — Redis 连接 URL
- **DATABASE_URL** — PostgreSQL 异步连接 URL（asyncpg 驱动）
- **CELERY_BROKER_URL** — Celery Broker 地址（Redis）
- **CORS_ORIGINS** — CORS 允许的源（逗号分隔，默认 `*`）

---

## 11. 文档索引

- **[01-overview.md](01-overview.md)**（本文档） — 项目总览、技术栈、模块分层、核心流程、认证、限流、BigInt 修复、前端页面
- **[02-framework.md](02-framework.md)** — 框架层：FastAPI 路由 / 依赖注入 / 中间件 / SSE
- **[03-infra-ai.md](03-infra-ai.md)** — AI 基础设施层：LLM / Embedding / 模型选择
- **[04-rag-core.md](04-rag-core.md)** — RAG 核心：RAGChain / 检索 / Prompt 组装
- **[05-ingestion.md](05-ingestion.md)** — 入库管线：Celery Tasks / Pipeline / Nodes
- **[06-concurrency.md](06-concurrency.md)** — 并发模型：async/await / Celery / 限流策略
- **[07-data-model.md](07-data-model.md)** — 数据模型：PostgreSQL 表结构 / pgvector / Snowflake ID
- **[08-deployment.md](08-deployment.md)** — 部署架构：Docker Compose / Nginx / 监控
- **[09-auth-security.md](09-auth-security.md)** — 认证与安全：JWT / 部门权限 / 限流 / SafeJSON
- **[10-frontend.md](10-frontend.md)** — 前端架构：React 18 / TypeScript / Vite / 页面说明
- **[11-api-reference.md](11-api-reference.md)** — API 参考：全部接口端点 / 请求响应格式
