# Ragent Python 版 — 部署架构与可观测性

## 1. 部署架构

### 1.1 单机部署（开发/小规模）

```
┌─────────────────────────────────────────────────────────────────────┐
│                         单机部署架构                                  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │  Uvicorn + FastAPI                                           │ │
│  │  (uvloop 加速, 多 worker 进程)                               │ │
│  │                                                              │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐ │ │
│  │  │ Worker 1 │  │ Worker 2 │  │ Worker 3 │  │ Worker N   │ │ │
│  │  └──────────┘  └──────────┘  └──────────┘  └────────────┘ │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                              │                                    │
│  ┌──────────┐  ┌────────────┼────────────┐  ┌──────────────┐    │
│  │ PostgreSQL│  │   Redis    │            │  │  外部模型服务  │    │
│  │ +pgvector │  │ 缓存/限流   │            │  │ 智谱AI /      │    │
│  │           │  │ Celery     │            │  │ 硅基流动      │    │
│  └──────────┘  │ Broker/Result│           │  └──────────────┘    │
│                 └──────────────┘           │                       │
│                                            │                       │
│  ┌───────────────────────────────────────┐ │                       │
│  │ Celery Worker                         │ │                       │
│  │ (后台异步任务处理)                     │ │                       │
│  │ - ingestion.task 队列                 │ │                       │
│  │ - ingestion.chunk 队列                │ │                       │
│  │ - rag.feedback 队列                   │ │                       │
│  └───────────────────────────────────────┘ │                       │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ 前端服务                                                       │ │
│  │ (React + Nginx 静态资源)                                       │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 容器化部署（生产推荐）

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      Docker Compose 编排（7 个服务）                       │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  核心服务层                                                         │ │
│  │  ┌────────────────────┐ ┌────────────────────┐ ┌─────────────────┐ │ │
│  │  │    postgres        │ │      redis         │ │   ragent-api    │ │ │
│  │  │  PostgreSQL 16     │ │  Redis 7 Alpine    │ │  FastAPI 应用    │ │ │
│  │  │  + pgvector 扩展   │ │  缓存/限流/Celery  │ │  - uvicorn       │ │ │
│  │  │  数据持久化        │ │  Broker/Result     │ │  - 多 worker     │ │ │
│  │  │  :5432             │ │  :6379             │ │  - /health       │ │ │
│  │  │                    │ │                    │ │  - /metrics      │ │ │
│  │  └────────────────────┘ └────────────────────┘ └─────────────────┘ │ │
│  └────────────────────────┬───────────────────────────────────────────┘ │
│                           │                                              │
│  ┌───────────────────────┼────────────────────────────────────────────┐ │
│  │  异步任务处理          │  前端与监控                                  │ │
│  │  ┌──────────────────┐ │ ┌──────────────────┐ ┌──────────────────┐ │ │
│  │  │  ragent-worker   │ │ │   ragent-web     │ │   prometheus     │ │ │
│  │  │  Celery Worker   │ │ │  React + Nginx   │ │  指标采集        │ │ │
│  │  │  - 4 个队列      │ │ │  静态资源服务     │ │  :9090           │ │ │
│  │  │  - ingestion.task│ │ │  :80             │ │  prom.yml 挂载    │ │ │
│  │  │  - ingestion.chunk│ │ │  API 反向代理    │ │                   │ │ │
│  │  │  - rag.feedback  │ │ │                  │ │                   │ │ │
│  │  └──────────────────┘ │ └──────────────────┘ └──────────────────┘ │ │
│  │                       │                                            │ │
│  │                       │ ┌──────────────────┐                      │ │
│  │                       │ │    grafana       │                      │ │
│  │                       │ │  可视化仪表盘     │                      │ │
│  │                       │ │  :3000           │                      │ │
│  │                       │ └──────────────────┘                      │ │
│  └───────────────────────┼────────────────────────────────────────────┘ │
│                          │                                               │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  存储卷与网络                                                       │ │
│  │  ┌──────────────────────────────────────────────────────────────┐ │ │
│  │  │  命名卷：                                                      │ │ │
│  │  │  - ragent-redis-data     (redis 持久化)                        │ │ │
│  │  │  - ragent-postgres-data  (PostgreSQL 数据)                     │ │ │
│  │  │  - ragent-upload-data    (上传文件存储)                        │ │ │
│  │  │  - ragent-prometheus-data (Prometheus 时序数据)                │ │ │
│  │  │  - ragent-grafana-data   (Grafana 配置)                        │ │ │
│  │  └──────────────────────────────────────────────────────────────┘ │ │
│  │                                                                   │ │
│  │  ┌──────────────────────────────────────────────────────────────┐ │ │
│  │  │  网络隔离：                                                    │ │ │
│  │  │  ├── ragent-net — 所有服务共享 bridge 网络                   │ │ │
│  │  │  └── 对外端口：API :8000 / Web :80 / Grafana :3000           │ │ │
│  │  └──────────────────────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Docker Compose 服务清单

docker-compose.yml 定义了 7 个服务，完整列表如下：

- **redis** — Redis 7 Alpine，多数据库分区（db0 缓存、db1 Celery Broker、db2 Celery Result Backend），挂载 `redis-data` 命名卷
- **postgres** — PostgreSQL 16 Alpine，挂载 `postgres-data` 命名卷，内置 pgvector 扩展
- **ragent-api** — FastAPI 应用容器，挂载 `upload-data` 卷到 `/data/pdfs`，暴露 Prometheus `/metrics` 端点
- **ragent-worker** — Celery Worker 容器，订阅 4 个队列处理异步任务（详见第 3 节），与 API 共享 `upload-data` 卷
- **ragent-web** — React 前端 + Nginx 反向代理，构建自 `./frontend` 目录
- **prometheus** — 指标采集服务，挂载本地 `prometheus.yml` 配置文件（只读），挂载 `prometheus-data` 命名卷持久化时序数据
- **grafana** — 可视化仪表盘，挂载 `grafana-data` 命名卷持久化面板配置

### 2.1 命名卷清单

所有持久化数据通过 Docker named volume 管理：

- **redis-data** (`ragent-redis-data`) — Redis AOF 持久化 + RDB 快照
- **postgres-data** (`ragent-postgres-data`) — PostgreSQL 数据文件
- **upload-data** (`ragent-upload-data`) — 用户上传文件存储，挂载到容器内 `/data/pdfs`
- **prometheus-data** (`ragent-prometheus-data`) — Prometheus 时序数据
- **grafana-data** (`ragent-grafana-data`) — Grafana 仪表盘与配置

**upload-data 卷说明：**

`upload-data` 是 API 和 Worker 共享的命名卷，API 服务接收用户上传的 PDF/文档后写入 `/data/pdfs`，Worker 服务从同一目录读取文件进行入库处理。使用命名卷而非绑定挂载，确保容器重启和跨环境部署时数据不丢失。

---

## 3. Worker 队列配置

Celery Worker 通过 `-Q` 参数订阅以下 4 个队列：

- **ingestion.task** — 文档入库主任务，接收上传文件触发的入库请求，协调整体入库流程
- **ingestion.chunk** — 文档分块任务，负责文本提取后的分块、向量化、写入 pgvector
- **rag.feedback** — RAG 反馈处理任务，处理用户对问答结果的点赞/点踩反馈
- **celery** — 默认队列，处理不属于上述专属队列的通用异步任务

启动命令：

```
celery -A ragent.common.celery_worker worker -l info -c 4 -Q ingestion.task,ingestion.chunk,rag.feedback,celery
```

并发数 `-c 4` 表示 4 个 worker 进程，可根据服务器 CPU 核数调整。

---

## 4. 环境变量完整清单

所有配置项通过 pydantic-settings 管理，支持环境变量覆盖。配置优先级从高到低：

1. 系统环境变量
2. 项目根目录 `.env` 文件
3. 代码内默认值

### 4.1 AI 模型配置

- **GLM_API_KEY** — 智谱 AI API Key，用于 LLM 聊天模型调用（必填）
- **GLM_BASE_URL** — API Base URL，默认 `https://open.bigmodel.cn/api/coding/paas/v4`
- **GLM_MODEL** — 默认聊天模型（litellm 格式），默认 `openai/glm-4-flash`
- **EMBEDDING_MODEL** — Embedding 模型（litellm 格式），默认 `openai/Qwen/Qwen3-Embedding-8B`
- **SILICONFLOW_API_KEY** — 硅基流动 API Key，用于 Embedding 模型调用（必填）
- **SILICONFLOW_API_BASE** — 硅基流动 API Base URL，默认 `https://api.siliconflow.cn/v1`

### 4.2 数据库配置

- **DATABASE_URL** — PostgreSQL 异步连接 URL（asyncpg 驱动），格式 `postgresql+asyncpg://user:pass@host:port/db`

### 4.3 缓存配置

- **REDIS_URL** — Redis 连接 URL，格式 `redis://host:port/db`，默认 `redis://localhost:6379/0`

### 4.4 Celery 配置

- **CELERY_BROKER_URL** — Celery Broker 地址，默认 `redis://localhost:6379/1`
- **CELERY_RESULT_BACKEND** — Celery 结果后端，默认 `redis://localhost:6379/2`

Redis 多数据库分区说明：db0 用于应用缓存/限流，db1 用作 Celery Broker，db2 用作 Celery Result Backend。

### 4.5 JWT 鉴权配置

- **JWT_SECRET_KEY** — JWT 签名密钥（生产环境务必更换）
- **JWT_ALGORITHM** — JWT 签名算法，默认 `HS256`
- **JWT_ACCESS_TOKEN_EXPIRE_MINUTES** — Access Token 过期时间（分钟），默认 1440（24 小时）

### 4.6 应用配置

- **APP_NAME** — 应用名称，默认 `Ragent`
- **APP_VERSION** — 应用版本，默认 `0.1.0`
- **DEBUG** — 调试模式开关（bool），默认 `False`
- **LOG_LEVEL** — 日志级别，默认 `INFO`
- **API_PREFIX** — API 路由前缀，默认 `/api/v1`
- **CORS_ORIGINS** — CORS 允许的源，多个用逗号分隔，默认 `*`

### 4.7 向量存储配置（当前）

当前版本使用 PostgreSQL + pgvector 作为向量存储引擎，所有向量数据存储在 PostgreSQL 数据库中。

配置项：
- **POSTGRESQL_HOST** — PostgreSQL 主机地址，通过 `DATABASE_URL` 设置
- **POSTGRESQL_PORT** — PostgreSQL 端口，通过 `DATABASE_URL` 设置
- **PGVECTOR_EXTENSION** — pgvector 扩展自动安装

> 注意：未来如需迁移到专用向量数据库（如 Milvus），可在 `settings.py` 中添加相应配置项。

### 4.8 LLM 与 RAG 参数

- **LLM_TIMEOUT** — LLM 请求超时秒数
- **LLM_MAX_RETRIES** — 最大重试次数
- **EMBEDDING_DIMENSION** — 向量维度
- **CHUNK_SIZE** — 文本分块大小
- **CHUNK_OVERLAP** — 分块重叠字符数
- **RETRIEVAL_TOP_K** — 检索返回数量

### 4.9 限流与会话配置

- **RATE_LIMIT_MAX_CONCURRENT** — 最大并发请求数
- **RATE_LIMIT_WINDOW_SECONDS** — 限流窗口秒数
- **SESSION_MAX_ROUNDS** — 会话最大轮次
- **SESSION_SUMMARY_THRESHOLD** — 会话摘要触发轮次

---

## 5. 前端服务 ragent-web

前端基于 React 构建，通过 Nginx 提供静态资源服务和 API 反向代理：

**构建流程：**

1. Dockerfile 中使用 Node 镜像执行 `npm run build` 生成静态资源
2. 将构建产物复制到 Nginx 镜像
3. Nginx 配置静态资源服务 + API 请求反向代理到 `ragent-api:8000`

**服务配置：**

- 构建上下文：`./frontend` 目录
- 对外端口：`80`
- 依赖：`ragent-api` 服务
- 网络：`ragent-net`

---

## 6. Prometheus 采集配置

Prometheus 服务负责采集各服务的运行指标：

**挂载配置：**

- `./prometheus.yml:/etc/prometheus/prometheus.yml:ro` — 采集配置文件（只读挂载）
- `prometheus-data:/prometheus` — 时序数据持久化

**采集目标：**

- `ragent-api:8000/metrics` — FastAPI 应用的 Prometheus 指标（通过 `prometheus_fastapi_instrumentator` 暴露）

**部署配置：**

- 镜像：`prom/prometheus:latest`
- 对外端口：`9090`
- 依赖：`ragent-api`

---

## 7. Grafana 可视化

Grafana 提供 metrics 的可视化仪表盘：

**持久化：**

- `grafana-data:/var/lib/grafana` — 命名卷持久化仪表盘、数据源配置、用户设置
- 容器重启后面板配置不丢失

**默认凭据：**

- 管理员用户名：`admin`
- 管理员密码：通过 `GF_SECURITY_ADMIN_PASSWORD` 环境变量配置

**部署配置：**

- 镜像：`grafana/grafana:latest`
- 对外端口：`3000`
- 依赖：`prometheus`

---

## 8. 部署拓扑对比

- **应用服务** — 开发环境：单 worker；生产环境：多 worker + 多副本
- **PostgreSQL** — 开发环境：单实例 + pgvector；生产环境：主从 + pgvector
- **Redis** — 开发环境：单实例；生产环境：Sentinel 哨兵模式（同时用作缓存、限流和 Celery Broker）
- **消息队列** — 开发环境：Redis 作为 Celery Broker；生产环境：RabbitMQ 或 Redis Cluster（可选）
- **向量存储** — 开发环境：pgvector；生产环境：pgvector（或专用向量数据库如 Milvus）

---

## 9. 可观测性

### 9.1 全链路追踪

基于装饰器驱动的 Trace 框架，覆盖 RAG 问答的每一个环节：

```
@rag_trace_root("rag-chat")
  ├── @rag_trace_node("query-rewrite")     — 问题重写耗时
  ├── @rag_trace_node("intent-classify")   — 意图分类耗时
  ├── @rag_trace_node("retrieval")         — 多路检索耗时
  │     ├── @rag_trace_node("channel-1")   — 单通道耗时
  │     └── @rag_trace_node("channel-2")   — 单通道耗时
  ├── @rag_trace_node("rerank")            — 重排序耗时
  ├── @rag_trace_node("prompt-build")      — Prompt 组装耗时
  └── @rag_trace_node("llm-generate")      — LLM 生成耗时
```

每次 Trace 产生两类记录：

- **t_rag_trace_run** — 记录整体运行信息：trace_id、状态、总耗时、错误信息
- **t_rag_trace_node** — 记录每个节点的详细信息：支持树形嵌套、深度、模块名、函数名、额外数据

### 9.2 入库过程追踪

```
┌───────────────────────────────────────────────────────────┐
│  入库追踪体系                                              │
│                                                            │
│  任务级 (t_ingestion_task):                                │
│  ├── 整体状态 (PENDING/RUNNING/COMPLETED/FAILED)          │
│  ├── 总耗时                                                │
│  └── 错误信息                                              │
│                                                            │
│  节点级 (t_ingestion_task_node):                           │
│  ├── 每个节点的独立执行记录                                │
│  ├── 节点状态 / 耗时 / 输出                                │
│  └── 错误详情                                              │
│                                                            │
│  分块级 (t_knowledge_document_chunk_log):                  │
│  ├── 提取耗时 / 分块耗时 / 向量化耗时 / 持久化耗时        │
│  └── 分块数量 / 错误信息                                   │
└───────────────────────────────────────────────────────────┘
```

### 9.3 结构化日志

```
┌──────────────────────────────────────────────────────────┐
│  日志规范                                                 │
│                                                           │
│  输出格式: JSON                                           │
│  ├── timestamp  — 时间戳                                  │
│  ├── level      — 日志级别 (DEBUG/INFO/WARNING/ERROR)    │
│  ├── trace_id   — 链路追踪ID (自动绑定)                   │
│  ├── module     — 模块名                                  │
│  ├── message    — 日志消息                                 │
│  └── extra      — 扩展字段 (模型名/耗时/状态码等)         │
│                                                           │
│  日志级别使用规范:                                        │
│  ├── DEBUG   — 开发调试信息                               │
│  ├── INFO    — 关键业务节点 (请求进入/检索完成/入库完成)   │
│  ├── WARNING — 可恢复的异常 (模型切换/检索超时)           │
│  └── ERROR   — 不可恢复错误 (数据库连接失败/全链路失败)    │
└──────────────────────────────────────────────────────────┘
```

---

## 10. 健康检查

```
┌──────────────────────────────────────────────────────────┐
│  /health 端点                                             │
│                                                           │
│  检查项:                                                  │
│  ├── PostgreSQL 连接     → SELECT 1                       │
│  ├── Redis 连接          → PING                           │
│  ├── 磁盘空间            → 临时目录可用空间                │
│  └── Celery Worker 状态  → 心跳检查（可选）                │
│                                                           │
│  响应:                                                    │
│  ├── 200  — 全部正常                                      │
│  └── 503  — 部分组件不可用（附带详情）                     │
└──────────────────────────────────────────────────────────┘
```

---

## 11. 与 Java 版的简化对比

- **前端** — Java 版：React 18 完整前端；Python 版：React + Nginx 容器化前端（ragent-web）
- **鉴权** — Java 版：Sa-Token；Python 版：JWT Token 鉴权
- **MCP Server** — Java 版：独立 Spring Boot 进程；Python 版：内聚到主应用
- **消息队列** — Java 版：RocketMQ 5.x；Python 版：Redis（Celery Broker）
- **入库路线** — Java 版：同步 + MQ 两条路线；Python 版：仅 MQ 异步路线
- **线程模型** — Java 版：8 个独立线程池；Python 版：asyncio 协程
- **文档解析** — Java 版：Apache Tika；Python 版：pdfplumber + python-docx 等
- **模型调用** — Java 版：自定义 ChatClient 体系；Python 版：litellm 统一封装
- **熔断器** — Java 版：自定义 ModelHealthStore；Python 版：circuitbreaker 库
- **容器化** — Java 版：未明确；Python 版：Docker Compose 一键部署（7 服务编排）
