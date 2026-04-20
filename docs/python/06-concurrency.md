# Ragent Python 版 — 并发模型与限流机制

## 1. 模块定位 — 并发模型概述

Python 版采用 **双进程并发模型**，由两类独立进程协作完成：

- **FastAPI 主进程** —— 并发机制：asyncio 事件循环（uvicorn），职责：HTTP/SSE 请求处理、RAG 问答链路
- **Celery Worker 进程** —— 并发机制：独立进程，内部通过 `asyncio.run()` 驱动异步管线，职责：文档摄入管线（fetcher→parser→enhancer→chunker→enricher→indexer）

**核心区别于 Java 版**：Python 版不使用线程池处理请求，而是协程（asyncio）+ 进程级 Worker（Celery）的组合。

---

## 2. 并发调度架构

### 2.1 整体架构图

```
┌──────────────────────────────────────────────────────────────┐
│                     FastAPI 主进程 (uvicorn)                  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  asyncio 事件循环                                      │  │
│  │                                                        │  │
│  │  ├── HTTP 请求处理（FastAPI 路由协程）                   │  │
│  │  ├── SSE 流式推送（异步生成器）                         │  │
│  │  ├── Redis 操作（redis-py async pipeline）              │  │
│  │  ├── 数据库操作（SQLAlchemy async session）             │  │
│  │  ├── 外部模型调用（httpx async）                       │  │
│  │  └── 并行检索调度（asyncio.gather）                    │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  中间件栈（注册顺序 = Starlette 添加顺序）：                   │
│  1. CORSMiddleware              —— 跨域资源共享              │
│  2. RateLimitMiddleware         —— IP 滑动窗口限流           │
│  3. ExceptionHandlerMiddleware  —— 全局异常捕获              │
│  4. RequestContextMiddleware    —— 请求上下文注入            │
│  5. TraceMiddleware             —— 链路追踪                 │
└──────────────────────────────────────────────────────────────┘

         │ Redis (broker)          │ Redis (result backend)
         ▼                        ▼
┌──────────────────────────────────────────────────────────────┐
│                  Celery Worker 进程（独立）                    │
│                                                              │
│  ├── 从 Redis broker 拉取任务 (ingestion.task 队列)           │
│  ├── asyncio.run() 驱动异步管线执行                           │
│  ├── IngestionPipeline:                                       │
│  │   fetcher → parser → enhancer → chunker → enricher        │
│  │   → indexer                                                │
│  ├── 回写 chunk_count 到 PostgreSQL (asyncpg)                 │
│  └── 结果序列化写入 Redis result backend                      │
│                                                              │
│  任务配置:                                                    │
│  ├── time_limit=600      (硬超时 10 分钟)                    │
│  ├── soft_time_limit=540 (软超时 9 分钟)                     │
│  ├── acks_late=True      (任务完成后才确认)                  │
│  └── track_started=True  (记录开始状态)                      │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 FastAPI 主进程

FastAPI 进程由 uvicorn 驱动，所有 I/O 密集型操作（数据库、Redis、HTTP 调用、向量检索）均以协程方式运行在同一个 asyncio 事件循环中，**不使用 aio-pika 消费者**。消息队列的交互完全委托给 Celery Worker。

启动方式：

```bash
uvicorn ragent.main:app --host 0.0.0.0 --port 8000 --workers 4
```

应用工厂 `create_app()` 在 `main.py` 中完成：
- CORS 中间件配置
- `RateLimitMiddleware` 注册（优先拦截超限请求）
- 异常处理、请求上下文、链路追踪中间件
- Prometheus 指标暴露（`/metrics` 端点）
- 生命周期管理：启动时初始化 PostgreSQL 和 Redis 连接池，关闭时释放资源

### 2.3 中间件注册顺序

**实现文件**：`src/ragent/main.py` → `create_app()`

中间件按以下顺序注册（即 `app.add_middleware()` 的调用顺序）。在 Starlette 中，后注册的中间件先执行，因此请求进入时的实际执行顺序与注册顺序相反：

**注册顺序**（`create_app()` 中的代码顺序）：

1. **CORSMiddleware** —— 跨域资源共享，最先注册
2. **RateLimitMiddleware** —— IP 滑动窗口限流，拦截超限请求
3. **ExceptionHandlerMiddleware** —— 全局异常捕获，兜底返回统一错误格式
4. **RequestContextMiddleware** —— 提取 `X-User-Id` / `X-Username` / `X-User-Role` 到 ContextVar
5. **TraceMiddleware** —— 链路追踪，生成/传播 trace_id，最后注册

**请求进入时的执行顺序**：TraceMiddleware → RequestContextMiddleware → ExceptionHandlerMiddleware → RateLimitMiddleware → CORSMiddleware → 路由处理函数

注册代码：

```python
# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate Limiting（拦截超限请求）
from ragent.app.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# 异常处理 → 上下文注入 → 链路追踪
app.add_middleware(ExceptionHandlerMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(TraceMiddleware)
```

### 2.4 Celery Worker 进程

Celery Worker 是独立进程，通过 Redis broker 接收任务：

```bash
celery -A ragent.ingestion.tasks worker -Q ingestion.task -c 2
```

关键配置（`celery_app.py`）：

- **`broker_url`**：`redis://localhost:6379/1` —— Redis 作为消息 broker
- **`result_backend`**：`redis://localhost:6379/2` —— 任务结果存储
- **`task_serializer`**：`json` —— 任务参数序列化格式
- **`result_serializer`**：`json` —— 结果序列化格式
- **`timezone`** / **`enable_utc`**：`Asia/Shanghai` / `True`

任务定义示例：

```python
@celery_app.task(
    bind=True,
    name="ragent.ingestion.run_pipeline",
    queue=INGESTION_TASK_QUEUE,
    time_limit=600,
    soft_time_limit=540,
)
def run_ingestion_pipeline(self, task_id, pipeline_id, source_type, source_location, ...):
    # 同步函数中通过 asyncio.run() 驱动异步管线
    ctx = asyncio.run(_run_pipeline_and_update())
    return _serialize_context(ctx)
```

Worker 进程内部通过 `asyncio.run()` 创建独立的事件循环来执行异步管线，完成后将结果序列化写回 Redis result backend。

---

## 3. 限流机制

系统实现**两层限流**：

- **IP 级别速率限制** —— 实现：`RateLimitMiddleware`（FastAPI 中间件），作用：保护 HTTP 端点，防刷防暴力
- **用户级排队控制** —— 实现：`RateLimiter`（并发模块），作用：控制同时执行的用户请求数

### 3.1 IP 级别速率限制（HTTP 中间件）

**实现文件**：`src/ragent/app/rate_limit.py`

**算法**：基于 Redis Sorted Set 的滑动窗口算法，按 `IP + 路径前缀` 分组限流。

#### 3.1.1 限流规则类 — RateLimitRule

`RateLimitRule` 是单条限流规则的封装，使用 `__slots__` 优化内存：

```python
class RateLimitRule:
    """单条限流规则。"""

    __slots__ = ("prefix", "max_requests", "window_seconds", "methods")

    def __init__(
        self,
        prefix: str,
        max_requests: int,
        window_seconds: int,
        methods: set[str] | None = None,
    ):
        self.prefix = prefix          # URL 路径前缀，用于匹配请求
        self.max_requests = max_requests  # 窗口内最大请求数
        self.window_seconds = window_seconds  # 滑动窗口大小（秒）
        self.methods = methods        # 限流的 HTTP 方法集合，None 表示所有方法
```

**属性说明**：

- **`prefix`**（`str`）—— URL 路径前缀，通过 `startswith()` 前缀匹配请求路径
- **`max_requests`**（`int`）—— 滑动窗口内允许的最大请求数
- **`window_seconds`**（`int`）—— 滑动窗口的时间跨度（秒）
- **`methods`**（`set[str] | None`）—— 需要限流的 HTTP 方法集合（如 `{"POST"}`），为 `None` 时对所有方法生效

#### 3.1.2 默认限流规则

系统预定义 4 条限流规则，保护关键端点：

- **注册端点** `/api/v1/auth/register` —— 限制：5 次/60 秒，方法：POST，目的：防刷号
- **登录端点** `/api/v1/auth/login` —— 限制：10 次/60 秒，方法：POST，目的：防暴力破解
- **聊天端点** `/api/v1/chat` —— 限制：20 次/60 秒，方法：POST，目的：防滥用
- **上传端点** `/api/v1/documents/upload` —— 限制：10 次/60 秒，方法：POST，目的：防大量上传

定义代码：

```python
DEFAULT_RULES: list[RateLimitRule] = [
    RateLimitRule("/api/v1/auth/register", max_requests=5,  window_seconds=60, methods={"POST"}),
    RateLimitRule("/api/v1/auth/login",    max_requests=10, window_seconds=60, methods={"POST"}),
    RateLimitRule("/api/v1/chat",          max_requests=20, window_seconds=60, methods={"POST"}),
    RateLimitRule("/api/v1/documents/upload", max_requests=10, window_seconds=60, methods={"POST"}),
]
```

#### 3.1.3 限流中间件 — RateLimitMiddleware

`RateLimitMiddleware` 继承自 `BaseHTTPMiddleware`，拦截每个 HTTP 请求并检查是否超过对应路径的限流阈值。

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    """IP 级别速率限制中间件。"""

    def __init__(self, app: Any, rules: list[RateLimitRule] | None = None):
        super().__init__(app)
        self._rules = rules or DEFAULT_RULES
```

**核心方法**：

- **`_match_rule(request)`** —— 遍历规则列表，使用 `path.startswith(rule.prefix)` 前缀匹配请求路径，同时检查 HTTP 方法是否在 `rule.methods` 集合内。无匹配规则时返回 `None`，直接放行
- **`_get_client_ip(request)`** —— 从请求中提取客户端 IP，支持反向代理场景
- **`_check_rate_limit(ip, rule)`** —— 执行 Redis 滑动窗口检查，返回 `True`（允许）或 `False`（超限）

#### 3.1.4 路径匹配逻辑

规则匹配使用 **前缀匹配**（`startswith`），而非精确匹配。这意味着：

- 规则 `/api/v1/chat` 会匹配 `/api/v1/chat`、`/api/v1/chat/123`、`/api/v1/chat/stream` 等所有以该前缀开头的路径
- 同时会检查 HTTP 方法，仅当请求方法在 `rule.methods` 集合中（或 `methods` 为 `None`）时才命中规则
- 规则按列表顺序遍历，首次匹配即返回（**先匹配先生效**）

```python
def _match_rule(self, request: Request) -> RateLimitRule | None:
    path = request.url.path
    method = request.method.upper()
    for rule in self._rules:
        if path.startswith(rule.prefix):
            if rule.methods is None or method in rule.methods:
                return rule
    return None
```

#### 3.1.5 IP 提取逻辑

支持反向代理部署场景，按优先级依次尝试：

1. **`X-Forwarded-For`** 请求头 —— 取第一个 IP（逗号分隔的客户端 IP 列表中最左侧为真实客户端 IP）
2. **`X-Real-IP`** 请求头 —— Nginx 等反向代理设置的客户端真实 IP
3. **`request.client.host`** —— Starlette 直接连接的客户端地址
4. 以上均无 —— 返回 `"unknown"`

```python
@staticmethod
def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"
```

#### 3.1.6 滑动窗口算法流程

```
请求到达
   │
   ▼
┌──────────────────────────────────────────┐
│  1. 匹配限流规则（路径前缀 + HTTP 方法）   │
│     无匹配规则 → 直接放行                  │
└───────────────┬──────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────┐
│  2. 获取客户端 IP                         │
│     X-Forwarded-For → X-Real-IP          │
│     → request.client.host → "unknown"    │
└───────────────┬──────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────┐
│  3. Redis Pipeline 原子操作（4 步）                        │
│                                                          │
│  Key: ratelimit:{ip}:{path_prefix}                       │
│  Sorted Set: score=timestamp, member="{ts}:{uuid}"       │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ ZREMRANGEBYSCORE key -inf {window_start}            │  │
│  │   → 清除窗口外的旧记录（滑动窗口核心）              │  │
│  ├────────────────────────────────────────────────────┤  │
│  │ ZADD key {now} "{now}:{uuid}"                       │  │
│  │   → 添加当前请求到有序集合                          │  │
│  ├────────────────────────────────────────────────────┤  │
│  │ ZCARD key                                           │  │
│  │   → 统计窗口内的请求数量                            │  │
│  ├────────────────────────────────────────────────────┤  │
│  │ EXPIRE key {window_seconds + 10}                    │  │
│  │   → 设置 key 过期（兜底清理，略大于窗口）           │  │
│  └────────────────────────────────────────────────────┘  │
└───────────────┬──────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────┐
│  4. 判断: count <= max_requests?          │
│     是 → 放行，调用后续中间件/路由        │
│     否 → 返回 HTTP 429 + Retry-After     │
└──────────────────────────────────────────┘
```

**Redis Pipeline 实现代码**：

```python
async def _check_rate_limit(self, ip: str, rule: RateLimitRule) -> bool:
    """检查是否超限。返回 True 表示允许，False 表示超限。"""
    # 获取 Redis 连接（含容错降级）
    from ragent.common.redis_manager import get_redis_manager
    manager = get_redis_manager()
    redis = manager.get_redis()

    now = time.time()
    window_start = now - rule.window_seconds
    key = f"ratelimit:{ip}:{rule.prefix}"
    member = f"{now}:{uuid.uuid4().hex[:8]}"

    pipe = redis.pipeline(transaction=True)
    pipe.zremrangebyscore(key, "-inf", window_start)   # 清除窗口外记录
    pipe.zadd(key, {member: now})                       # 添加当前请求
    pipe.zcard(key)                                     # 统计窗口内数量
    pipe.expire(key, rule.window_seconds + 10)           # 兜底过期
    results = await pipe.execute()

    count = results[2]  # ZCARD 结果
    return count <= rule.max_requests
```

#### 3.1.7 Redis Key 结构

```
ratelimit:192.168.1.100:/api/v1/chat
┌─────────────────────────────────────────────┐
│  Sorted Set                                  │
│                                              │
│  Score (timestamp)      Member               │
│  1700000001.234         1700000001.234:a1b2  │
│  1700000002.567         1700000002.567:c3d4  │
│  1700000003.890         1700000003.890:e5f6  │
│                                              │
│  TTL: 70s (窗口 60s + 10s 兜底)             │
└─────────────────────────────────────────────┘
```

**Key 格式**：`ratelimit:{ip}:{prefix}`
- **`ip`** —— 客户端 IP 地址（经 `_get_client_ip` 提取）
- **`prefix`** —— 匹配到的规则路径前缀（如 `/api/v1/chat`）

**Sorted Set 结构**：
- **Score** —— 请求到达时的 Unix 时间戳（浮点数，精确到毫秒）
- **Member** —— `{timestamp}:{uuid_hex_8位}`，UUID 保证同时间多请求的 member 唯一

#### 3.1.8 超限响应

当请求超过限流阈值时，中间件返回 **HTTP 429 Too Many Requests**，包含：

- **响应状态码**：`429`
- **响应头 `Retry-After`**：值为窗口秒数（如 `60`），告知客户端何时可以重试
- **响应体**：统一的 JSON 错误格式

```json
{
    "code": 429,
    "message": "请求过于频繁，请 60 秒后重试",
    "data": null,
    "trace_id": null,
    "timestamp": 1700000003.890
}
```

同时记录警告日志：`速率限制触发: ip=xxx, path=xxx, limit=xx/xxs`

#### 3.1.9 容错设计

- **Redis 不可用时**：`_check_rate_limit` 方法在获取 Redis 连接失败时捕获异常，**不阻塞请求**，直接返回 `True`（允许通过），仅记录警告日志 `"Redis 不可用，跳过限流检查"`
- **IP 提取支持反向代理**：依次尝试 `X-Forwarded-For` → `X-Real-IP` → `request.client.host`，兼容 Nginx/CDN 等部署场景
- **Key 自动过期**：每次检查时通过 `EXPIRE` 设置 `window_seconds + 10` 的 TTL，确保 Redis 中的临时数据不会无限累积

### 3.2 用户级排队控制（并发模块）

**实现文件**：`src/ragent/concurrency/rate_limiter.py`

用于控制同一用户的 RAG 问答并发请求数，采用 Redis ZSET 排队 + Lua 脚本原子判断 + `asyncio.Semaphore` 并发控制。

#### 工作流程

```
用户请求
   │
   ▼
┌───────────────────────────────────────┐
│  1. 请求入队                           │
│  Redis ZSET: ragent:queue:{user_id}   │
│  ZADD {request_id} {timestamp}        │
│  EXPIRE {queue_ttl=600s}              │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│  2. Lua 脚本原子位置检查               │
│  ZRANK → rank < max_concurrent?       │
│     是 → position=0 (就绪)            │
│     否 → position=N (排队位置)         │
└───────┬───────────────┬───────────────┘
        │               │
    就绪(position=0)  排队(position>0)
        │               │
        ▼               ▼
┌──────────────┐  ┌────────────────────┐
│ 获取 Semaphore│  │ 轮询等待            │
│ (带超时)      │  │ 每隔 poll_interval │
│              │  │ 重新检查位置        │
└──────┬───────┘  │ SSE 推送排队状态    │
       │          └────────┬───────────┘
       │                   │
       ▼                   ▼ (就绪后)
┌──────────────────────────────────────┐
│  执行 RAG 问答链路                    │
└───────────────┬──────────────────────┘
                │
                ▼
┌──────────────────────────────────────┐
│  释放: ZREM + Semaphore.release()    │
│  + PUBLISH 通知下一个等待者           │
└──────────────────────────────────────┘
```

#### 关键参数

- **`max_concurrent`**：默认值 `5` —— 同一用户最大并发执行数
- **`semaphore_timeout`**：默认值 `300s` —— 获取 Semaphore 超时时间
- **`queue_ttl`**：默认值 `600s` —— ZSET 键过期时间
- **`poll_interval`**：默认值 `1.0s` —— 排队轮询间隔

---

## 4. Prometheus 指标监控

**实现文件**：`src/ragent/main.py` → `create_app()`

### 4.1 集成方式

系统通过 `prometheus_fastapi_instrumentator` 库集成 Prometheus 指标采集，自动收集 HTTP 请求的延迟、流量、错误等指标。

### 4.2 配置详情

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator(
    should_group_status_codes=True,      # 按状态码类别分组（2xx/3xx/4xx/5xx）
    should_ignore_untemplated=True,      # 忽略未注册的路径模板
    excluded_handlers=["/api/v1/health"], # 排除健康检查端点
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=True)
```

**配置参数说明**：

- **`should_group_status_codes=True`** —— 将具体状态码按类别聚合（如 200、201、204 统一归为 2xx），减少指标基数
- **`should_ignore_untemplated=True`** —— 忽略未在 FastAPI 路由中注册的路径，避免未知路径污染指标
- **`excluded_handlers=["/api/v1/health"]`** —— 排除健康检查端点的指标采集，避免频繁的心跳请求干扰监控数据
- **`endpoint="/metrics"`** —— Prometheus 抓取端点路径
- **`include_in_schema=True`** —— 将 `/metrics` 端点包含在 OpenAPI 文档中

### 4.3 暴露端点

- **`GET /metrics`** —— Prometheus 标准抓取端点，返回文本格式的指标数据
- 该端点在 `create_app()` 中注册，**在所有中间件和路由配置完成后**初始化

### 4.4 采集的指标

`prometheus_fastapi_instrumentator` 默认采集以下指标：

- **`http_request_duration_seconds`** —— 请求延迟直方图（包含 `handler`、`method`、`status` 标签）
- **`http_requests_total`** —— 请求总数计数器
- **`http_request_size_bytes`** —— 请求体大小
- **`http_response_size_bytes`** —— 响应体大小

所有指标自动附带标签：
- **`method`** —— HTTP 方法（GET/POST/PUT/...）
- **`handler`** —— 路由处理函数路径
- **`status`** —— 响应状态码类别（2xx/3xx/4xx/5xx）

### 4.5 初始化时机

Prometheus Instrumentator 在 `create_app()` 中**最后初始化**，确保所有中间件和路由都已注册完毕后才开始采集指标：

```python
def create_app() -> FastAPI:
    app = FastAPI(...)
    # 1. CORS 中间件
    # 2. RateLimitMiddleware
    # 3. ExceptionHandler / RequestContext / TraceMiddleware
    # 4. 路由挂载
    app.include_router(auth_router)
    app.include_router(router)

    # 5. Prometheus —— 最后初始化
    Instrumentator(...).instrument(app).expose(...)

    return app
```

---

## 5. SSE 流式推送

**实现文件**：`src/ragent/common/sse.py`、`src/ragent/rag/chain.py`

### 5.1 SSE 事件模型

定义五种事件类型，通过异步生成器自然处理背压：

- **`meta`** —— 用途：元数据/阶段通知，data 格式：`{"status": "processing", "stage": "..."}`
- **`thinking`** —— 用途：AI 推理过程展示，data 格式：`{"content": "..."}`
- **`content`** —— 用途：实际生成内容片段，data 格式：`{"content": "..."}`
- **`error`** —— 用途：错误信息，data 格式：`{"message": "...", "code": "..."}`
- **`finish`** —— 用途：流结束标记，data 格式：`{}` 或附带统计信息

### 5.2 SSE 协议格式

```
event: {事件类型}
data: {JSON 载荷}

id: {可选事件ID}
retry: {可选重连间隔(ms)}
```

每个事件以两个空行结尾，符合 SSE 规范。

### 5.3 响应创建

`create_sse_response()` 封装 `StreamingResponse`，自动设置响应头：

```python
headers = {
    "Content-Type": "text/event-stream",   # SSE 标准媒体类型
    "Cache-Control": "no-cache",           # 禁止缓存
    "Connection": "keep-alive",            # 长连接
    "X-Accel-Buffering": "no",             # 禁用 Nginx 缓冲
}
```

### 5.4 RAG 链路中的 SSE 流

`RAGChain.ask()` 是一个异步生成器，按管线阶段依次 yield SSE 事件：

```
用户请求 → ask()
    │
    ├── yield sse_meta({"status": "processing"})
    │
    ├── 阶段 1: 加载历史记忆（静默）
    │
    ├── yield sse_meta({"stage": "query-rewrite"})
    │   └── 查询重写（失败则使用原始问题，不中断流）
    │
    ├── yield sse_meta({"stage": "intent-classify"})
    │   └── 意图分类（失败则降级为无意图，不中断流）
    │
    ├── yield sse_meta({"stage": "retrieval"})
    │   └── 检索（失败则返回空结果，不中断流）
    │
    ├── yield sse_meta({"stage": "prompt-build"})
    │   └── Prompt 组装
    │
    ├── yield sse_meta({"stage": "llm-generate"})
    │   └── 流式生成
    │       └── 逐 token yield sse_content(token)
    │
    ├── 保存到会话记忆（静默）
    │
    ├── yield sse_finish({conversation_id, intent, confidence, result_count})
    │
    └── 异常时: yield sse_error(message, code="B2001")
```

### 5.5 排队状态的 SSE 推送

用户级排队等待时，`RateLimiter.wait_for_turn()` 也通过 SSE 推送实时状态：

```
event: queue_status
data: {"position": 3, "status": "waiting"}

event: queue_status
data: {"position": 1, "status": "waiting"}

event: queue_status
data: {"position": 0, "status": "processing", "request_id": "xxx"}
```

---

## 6. 并行检索调度

**实现文件**：`src/ragent/rag/retrieval/retriever.py`

### 6.1 多通道并行检索

`RetrievalEngine` 使用 `asyncio.gather` 实现多通道并行向量检索：

```
查询文本
   │
   ▼
┌──────────────────┐
│  向量化 (embed)   │  ← EmbeddingService
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  构建检索通道                                  │
│                                              │
│  意图明确时:                                  │
│  ├── IntentDirectedChannel (意图关联集合)     │
│  └── GlobalVectorChannel   (全局补充)         │
│                                              │
│  意图不明确时:                                │
│  └── GlobalVectorChannel   (仅全局)           │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  asyncio.gather(*search_tasks)                │
│                                              │
│  ┌───────────────┐  ┌───────────────┐        │
│  │ 意图定向检索   │  │ 全局向量检索   │        │
│  │ (协程)        │  │ (协程)        │        │
│  └───────┬───────┘  └───────┬───────┘        │
│          │                  │                │
│          └────────┬─────────┘                │
│                   │                          │
│  return_exceptions=True                       │
│  → 单通道失败不阻塞其他通道                   │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  后处理器（串联执行）                  │
│                                      │
│  1. DeduplicatePostProcessor         │
│     基于 content_hash 去重           │
│     保留分数最高的条目                │
│     按分数降序排列                    │
│                                      │
│  2. RerankPostProcessor (Mock)       │
│     重排序（预留，当前直接截取 top_k）│
└──────────────┬───────────────────────┘
               │
               ▼
         最终检索结果
```

### 6.2 关键实现细节

**并行调度代码**：

```python
search_tasks = [channel.search(query_embedding, top_k=top_k) for channel in channels]
channel_results = await asyncio.gather(*search_tasks, return_exceptions=True)

# 容错：单通道失败不阻塞
for i, result in enumerate(channel_results):
    if isinstance(result, Exception):
        logger.warning("通道 %s 检索失败: %s", channels[i].__class__.__name__, result)
        continue
    all_results.extend(result)
```

**去重后处理器**：基于 `content_hash`（MD5），多个通道返回相同内容时仅保留分数最高的条目。

---

## 7. 超时与降级策略

### 7.1 超时层次

```
┌──────────────────────────────────────────────────────────┐
│  超时层次                                                 │
│                                                          │
│  ├── Celery 任务级超时                                    │
│  │   ├── time_limit=600       (硬超时 10 分钟，强制终止)  │
│  │   └── soft_time_limit=540  (软超时 9 分钟，抛异常)     │
│  │                                                        │
│  ├── 排队等待超时                                          │
│  │   └── RateLimiter.wait_for_turn(timeout=300)           │
│  │                                                        │
│  ├── Semaphore 获取超时                                    │
│  │   └── asyncio.wait_for(semaphore.acquire, timeout=300) │
│  │                                                        │
│  └── 向量化超时                                            │
│      └── embedding service 调用超时                       │
└──────────────────────────────────────────────────────────┘
```

### 7.2 降级策略

RAG 链路中每个子步骤均有独立的降级处理，**任何单步失败都不会中断整体 SSE 流**：

- **查询重写** —— 失败处理：`_rewrite_step` catch → return `None`，降级行为：使用原始问题继续
- **意图分类** —— 失败处理：`_classify_step` catch → return `None`，降级行为：无意图信息，退化为全局检索
- **检索** —— 失败处理：`_retrieval_step` catch → return `[]`，降级行为：空上下文，LLM 依赖自身知识
- **向量化** —— 失败处理：`embed()` catch → return `[]`，降级行为：返回空结果
- **单通道检索** —— 失败处理：`asyncio.gather(return_exceptions=True)`，降级行为：跳过失败通道，使用其他通道结果
- **LLM 生成** —— 失败处理：`_generate_step` catch → yield `sse_error`，降级行为：流中返回错误事件
- **保存记忆** —— 失败处理：`_save_memory` catch → 警告日志，降级行为：不影响已返回的响应
- **回写 chunk_count** —— 失败处理：`_update_doc_chunk_count_async` catch → 警告，降级行为：不影响管线执行结果

### 7.3 Redis 不可用时的容错

- **IP 限流中间件**：Redis 不可用时跳过限流检查，直接放行请求
- **排队限流器**：Redis 未初始化时抛出 `ServiceException`，不静默失败
- **连接池初始化**：Redis 初始化失败为**非致命错误**，不阻塞 FastAPI 应用启动

---

## 8. 与 Java 版的关键差异

- **并发模型** —— Java 版：8 个独立线程池；Python 版：asyncio 协程 + Celery Worker 进程
- **异步任务** —— Java 版：自建消息队列消费者；Python 版：Celery 框架 + Redis broker（独立 Worker 进程）
- **上下文透传** —— Java 版：`TransmittableThreadLocal`；Python 版：`contextvars.ContextVar`（协程原生）
- **IP 限流** —— Java 版：无；Python 版：Redis Sorted Set 滑动窗口（FastAPI 中间件）
- **排队控制** —— Java 版：Redisson Semaphore；Python 版：Redis ZSET + Lua + `asyncio.Semaphore` + Pub/Sub
- **SSE 推送** —— Java 版：`SseEmitter` + CAS 线程安全；Python 版：`StreamingResponse` + 异步生成器（天然线程安全）
- **并行调度** —— Java 版：`ThreadPoolExecutor` / `CompletableFuture`；Python 版：`asyncio.gather`（协程并行）
- **CPU 密集任务** —— Java 版：`ForkJoinPool`；Python 版：Celery Worker 独立进程（天然隔离）
- **管线异步驱动** —— Java 版：线程池提交；Python 版：`asyncio.run()` 在 Worker 进程中创建事件循环
- **错误降级** —— Java 版：异常捕获 + fallback；Python 版：每步骤独立 try/catch，逐步降级
