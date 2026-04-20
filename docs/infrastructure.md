# Ragent 系统架构文档

## 1. 项目概述

Ragent 是一个企业级 RAG（Retrieval-Augmented Generation）智能体平台，基于 **Python 3.12 + FastAPI + React 18** 构建。系统覆盖了从文档入库到智能问答的全链路能力，包括文档摄入管线、向量检索、意图识别、会话记忆、LLM 对话、全链路追踪等核心功能。

---

## 2. 技术栈总览

**后端运行时**
- Python 3.12
- FastAPI（ASGI Web 框架）
- Uvicorn（ASGI Server，4 workers）
- SQLAlchemy 2.0（async ORM）
- asyncpg（异步 PostgreSQL 驱动）
- Pydantic v2（数据校验与序列化）

**任务队列**
- Celery 5（分布式任务队列）
- Redis 7（Broker：db1，Result Backend：db2）

**数据库**
- PostgreSQL 16 + pgvector 扩展（关系数据 + 向量存储统一引擎）

**缓存**
- Redis 7（多 DB 复用：db0 缓存、db1 Celery Broker、db2 Celery Result）

**AI 服务**
- LLM：GLM（智谱AI）via litellm，模型 `openai/glm-4-flash`
- Embedding：硅基流动 Qwen/Qwen3-Embedding-8B

**前端**
- React 18 + TypeScript
- Vite（构建工具）
- Ant Design（UI 组件库）
- Nginx（生产静态资源服务）

**部署与运维**
- Docker Compose（7 个服务编排）
- Prometheus + Grafana（监控告警）

**认证安全**
- JWT（PyJWT 签发/验证）
- bcrypt（passlib 密码哈希）

**配置管理**
- pydantic-settings（环境变量 + .env 文件统一管理）

---

## 3. 系统整体架构

### 3.1 模块分层

```
┌──────────────────────────────────────────────────────────┐
│                     前端 (React 18)                       │
│            TypeScript + Vite + Ant Design                 │
└──────────────────────┬───────────────────────────────────┘
                       │ HTTP / REST API
┌──────────────────────▼───────────────────────────────────┐
│                  API 网关层 (FastAPI)                      │
│  ┌──────────┐ ┌─────────────┐ ┌───────────────────────┐  │
│  │  CORS    │→│ RateLimiter │→│ ExceptionHandler      │  │
│  └──────────┘ └─────────────┘ └───────────────────────┘  │
│  ┌──────────────────────┐ ┌───────────────────────────┐  │
│  │ RequestContext       │→│ TraceMiddleware           │  │
│  └──────────────────────┘ └───────────────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│                   应用层 (app/)                            │
│  ┌──────────┐ ┌──────────────┐ ┌──────────────────────┐  │
│  │ router   │ │ auth_router  │ │ deps (依赖注入)      │  │
│  │ (~815行) │ │ register/    │ │ CurrentUser/DbSession │  │
│  │ 主路由   │ │ login/me     │ │                       │  │
│  └──────────┘ └──────────────┘ └──────────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│                 公共层 (common/)                           │
│  ┌──────────┐ ┌────────────┐ ┌───────────────────────┐  │
│  │ models   │ │ snowflake  │ │ safe_json / json_utils│  │
│  │ (577行)  │ │ ID 生成器  │ │ BigInt精度修复        │  │
│  │ 17张表   │ │            │ │                       │  │
│  └──────────┘ └────────────┘ └───────────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│              基础设施层 (infra/)                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────────┐ │
│  │ database │ │  cache   │ │   auth (JWT + bcrypt)    │ │
│  │ asyncpg  │ │  Redis   │ │                          │ │
│  └──────────┘ └──────────┘ └──────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────┐ │
│  │               ai/ (AI 服务层)                        │ │
│  │  llm_service │ embedding_service │ model_selector   │ │
│  │              │                    │ circuit_breaker  │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│              摄入管线层 (ingestion/)                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────────┐ │
│  │ pipeline │ │  nodes   │ │   tasks (Celery)         │ │
│  │ 6节点引擎│ │ 6个节点  │ │ 异步任务+chunk_count回写  │ │
│  └──────────┘ └──────────┘ └──────────────────────────┘ │
│  ┌──────────────────────┐                                │
│  │ context (管线上下文)  │                                │
│  └──────────────────────┘                                │
└──────────────────────────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│              配置层 (config/)                              │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ settings.py — pydantic-settings, 环境变量/.env      │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### 3.2 各层职责说明

**API 网关层**
- 接收所有 HTTP 请求，按中间件链依次处理
- 中间件执行顺序：CORS → RateLimitMiddleware → ExceptionHandler → RequestContext → TraceMiddleware
- 将请求路由到对应的应用层处理函数

**应用层 (app/)**
- **router.py**（~815 行）：主路由，涵盖 health、chat、knowledge-bases、documents、upload、ingestion、conversations、departments 等端点
- **auth_router.py**：认证路由，提供 register、login、me 三个端点
- **deps.py**：FastAPI 依赖注入，提供 CurrentUser（当前用户）和 DbSession（数据库会话）
- **rate_limit.py**：基于 IP + Redis 滑动窗口的限流中间件
- **middleware.py**：Trace 追踪、RequestContext 上下文传递、ExceptionHandler 全局异常处理中间件

**公共层 (common/)**
- **models.py**（577 行）：定义全部 17 张数据库表，覆盖 5 个业务域
- **snowflake.py**：Snowflake 分布式 ID 生成器，保证 ID 全局唯一且有序
- **safe_json.py**：SafeJSONResponse，修复前端 JavaScript BigInt 精度丢失问题
- **json_utils.py**：LargeIntJSONEncoder，处理大整数 JSON 序列化

**基础设施层 (infra/)**
- **database.py**：基于 asyncpg + SQLAlchemy async session 的异步数据库连接管理
- **cache.py**：Redis 连接池管理，支持多 DB 复用
- **auth.py**：JWT 签发/验证 + bcrypt 密码哈希
- **ai/llm_service.py**：LLM 调用服务，通过 litellm 统一调用 GLM 模型
- **ai/embedding_service.py**：文本向量化服务，调用硅基流动 Embedding API
- **ai/model_selector.py**：模型选择器，根据场景选择合适的模型
- **ai/circuit_breaker.py**：熔断器，保护 AI 服务调用稳定性

**摄入管线层 (ingestion/)**
- **pipeline.py**：6 节点管线引擎，编排文档摄入全流程
- **nodes.py**：6 个独立节点实现（Fetcher/Parser/Enhancer/Chunker/Enricher/Indexer）
- **tasks.py**：Celery 异步任务定义 + asyncpg chunk_count 回写
- **context.py**：IngestionContext，管线执行上下文

**配置层 (config/)**
- **settings.py**：基于 pydantic-settings，统一管理环境变量和 .env 配置

---

## 4. 部署拓扑

### 4.1 Docker Compose 服务编排

系统通过 Docker Compose 编排 **7 个服务**，统一运行在 **ragent-net**（bridge 网络）下。

**服务 1：redis**
- 镜像：`redis:7-alpine`
- 端口：6379
- 用途：多 DB 复用——db0 缓存、db1 Celery Broker、db2 Celery Result Backend
- 存储：redis-data volume

**服务 2：postgres**
- 镜像：`postgres:16-alpine`
- 端口：5432
- 用途：关系数据库 + pgvector 向量存储
- 存储：postgres-data volume

**服务 3：ragent-api**
- 镜像：项目自建
- 端口：8000
- 命令：FastAPI + Uvicorn，4 workers 并发
- 用途：API 服务，处理所有 HTTP 请求
- 存储：upload-data volume（文件上传）

**服务 4：ragent-worker**
- 镜像：项目自建
- 命令：Celery worker，并发度 `-c 4`
- 队列：`ingestion.task`、`ingestion.chunk`、`rag.feedback`、`celery`
- 用途：异步任务执行，处理文档摄入、分块、RAG 反馈等后台任务

**服务 5：ragent-web**
- 镜像：项目自建
- 端口：80
- 用途：React 前端 + Nginx 静态资源服务

**服务 6：prometheus**
- 镜像：`prom/prometheus`
- 端口：9090
- 用途：指标采集与存储
- 存储：prometheus-data volume

**服务 7：grafana**
- 镜像：`grafana/grafana`
- 端口：3000
- 用途：监控可视化面板
- 存储：grafana-data volume

### 4.2 存储卷

- **redis-data**：Redis 持久化数据
- **postgres-data**：PostgreSQL 数据文件
- **prometheus-data**：Prometheus 时序数据
- **grafana-data**：Grafana 面板配置
- **upload-data**：用户上传文件存储

### 4.3 网络

- **ragent-net**：bridge 网络，所有 7 个服务共享此网络，通过容器名相互访问

### 4.4 服务通信关系

```
┌─────────────┐
│   用户浏览器  │
└──────┬──┬───┘
       │  │
       │  │ :80
       │  ▼
       │ ┌─────────────┐
       │ │ ragent-web  │  React + Nginx
       │ └──────┬──────┘
       │        │ 内部代理 /api → ragent-api:8000
       │        ▼
       │ ┌──────────────┐
       │ │ ragent-api   │  FastAPI + Uvicorn (4 workers)
       │ │   :8000      │
       │ └──┬───┬───┬───┘
       │    │   │   │
       │    │   │   └──────────────────────┐
       │    │   │                          │
       │    │   │ :5432                    │ Celery 任务派发
       │    │   ▼                          │ (Redis db1)
       │    │ ┌──────────────┐             │
       │    │ │  postgres    │             │
       │    │ │   :5432      │             │
       │    │ │ pgvector扩展 │             │
       │    │ └──────────────┘             │
       │    │                              │
       │    │ :6379                        ▼
       │    ├──────────────┐    ┌──────────────────┐
       │    │              │    │ ragent-worker    │
       │    ▼              │    │ Celery (-c 4)    │
       │  ┌──────────────┐│    │ 4个队列消费者     │
       │  │    redis     ││    └────┬───┬─────────┘
       │  │   :6379      ││         │   │
       │  │ db0/db1/db2  ││         │   │ :5432
       │  └──────────────┘│         │   ▼
       │                  │         │ ┌──────────────┐
       │                  └─────────┴─┤  postgres    │
       │                            └──┤              │
       │                               └──────────────┘
       │ :9090                    :6379
       ▼                           ▼
┌──────────────┐          ┌──────────────┐
│ prometheus   │◄─────────┤    redis     │
│   :9090      │  采集指标  │  (metrics)   │
└──────┬───────┘          └──────────────┘
       │ :3000
       ▼
┌──────────────┐
│   grafana    │
│   :3000      │
│  可视化面板   │
└──────────────┘
```

---

## 5. 中间件链

所有 API 请求按以下顺序经过中间件处理：

**第 1 层：CORS**
- 处理跨域请求，允许前端域名访问 API

**第 2 层：RateLimitMiddleware**
- 基于 IP + Redis 滑动窗口的限流机制
- 限流规则：
  - `/api/v1/auth/register`：5 次 / 60 秒
  - `/api/v1/auth/login`：10 次 / 60 秒
  - `/api/v1/chat`：20 次 / 60 秒
  - `/api/v1/upload`：10 次 / 60 秒

**第 3 层：ExceptionHandler**
- 全局异常捕获，统一错误响应格式

**第 4 层：RequestContext**
- 请求上下文初始化，传递请求级别数据

**第 5 层：TraceMiddleware**
- 请求追踪，生成 trace_id，贯穿全链路日志

---

## 6. 认证流程

### 6.1 用户注册

1. 客户端发送 `POST /api/v1/auth/register`，携带用户名和密码
2. 服务端使用 **bcrypt** 对密码进行哈希（通过 passlib）
3. 使用 **Snowflake ID 生成器** 生成唯一用户 ID
4. 将用户信息写入 `t_user` 表
5. 签发 **JWT Token**（通过 PyJWT）返回给客户端

### 6.2 用户登录

1. 客户端发送 `POST /api/v1/auth/login`，携带用户名和密码
2. 服务端从 `t_user` 查询用户，使用 **bcrypt** 验证密码
3. 验证通过后签发 **JWT Token** 返回给客户端

### 6.3 请求鉴权

1. 客户端在后续请求的 Header 中携带 `Authorization: Bearer <token>`
2. FastAPI 依赖注入 `CurrentUser` 通过 JWT 解码验证 Token 有效性
3. 从 Token 中提取用户信息，注入到请求处理函数中

---

## 7. 数据模型

系统共 **17 张表**，划分为 **5 个业务域**：

### 7.1 用户域

- **t_department** — 部门信息表
- **t_user** — 用户信息表（含 bcrypt 哈希密码）
- **t_conversation** — 对话会话表
- **t_message** — 对话消息表
- **t_conversation_summary** — 会话摘要表
- **t_message_feedback** — 消息反馈表（用户点赞/点踩）

### 7.2 知识库域

- **t_knowledge_base** — 知识库表
- **t_knowledge_document** — 知识库文档表
- **t_knowledge_chunk** — 文档分块表（含 pgvector 向量字段）
- **t_knowledge_document_chunk_log** — 分块日志表

### 7.3 RAG 意图域

- **t_intent_node** — 意图节点表
- **t_query_term_mapping** — 查询词映射表

### 7.4 追踪域

- **t_rag_trace_run** — RAG 追踪运行表
- **t_rag_trace_node** — RAG 追踪节点表

### 7.5 管线域

- **t_ingestion_pipeline** — 摄入管线定义表
- **t_ingestion_pipeline_node** — 管线节点定义表
- **t_ingestion_task** — 摄入任务表
- **t_ingestion_task_node** — 摄入任务节点执行记录表

---

## 8. 文档摄入管线

### 8.1 管线概述

文档摄入采用 **6 节点流水线** 架构，由 `pipeline.py` 管线引擎编排执行，每个节点独立实现于 `nodes.py`。

### 8.2 节点流程

```
文档上传
   │
   ▼
┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌──────────┐    ┌──────────┐
│ Fetcher │ →  │ Parser  │ →  │ Enhancer │ →  │ Chunker │ →  │ Enricher │ →  │ Indexer  │
│ 文件获取 │    │ 文档解析 │    │ 内容增强  │    │ 文本分块 │    │ 向量丰富  │    │ 向量索引  │
└─────────┘    └─────────┘    └──────────┘    └─────────┘    └──────────┘    └──────────┘
```

**节点 1：Fetcher（文件获取）**
- 从上传目录获取原始文档文件
- 验证文件格式和大小
- 将文件内容加载到内存

**节点 2：Parser（文档解析）**
- 解析不同格式的文档（PDF、Word、Markdown 等）
- 提取纯文本内容
- 保留文档结构信息

**节点 3：Enhancer（内容增强）**
- 对解析后的文本进行预处理
- 清洗噪声数据
- 标准化文本格式

**节点 4：Chunker（文本分块）**
- 将长文本按策略切分为多个 chunk
- 每个 chunk 保持语义完整性
- 记录分块元数据（位置、上下文等）

**节点 5：Enricher（向量丰富）**
- 调用硅基流动 Embedding API（Qwen/Qwen3-Embedding-8B）
- 为每个 chunk 生成向量表示
- 附加元数据信息

**节点 6：Indexer（向量索引）**
- 将 chunk 及其向量写入 PostgreSQL（通过 pgvector 扩展）
- 更新 `t_knowledge_chunk` 表
- 通过 asyncpg 回写 `chunk_count` 到文档记录

### 8.3 异步执行

- 管线通过 **Celery 异步任务** 执行（定义于 `tasks.py`）
- 任务分发到 4 个队列：`ingestion.task`、`ingestion.chunk`、`rag.feedback`、`celery`
- Celery Worker 并发度 `-c 4`，支持并行处理多个文档

---

## 9. AI 服务架构

### 9.1 LLM 服务

- **服务商**：智谱 AI（GLM）
- **调用方式**：通过 litellm 统一调用，模型标识 `openai/glm-4-flash`
- **功能**：智能对话、问题重写、意图识别、会话摘要生成

### 9.2 Embedding 服务

- **服务商**：硅基流动（SiliconFlow）
- **模型**：Qwen/Qwen3-Embedding-8B
- **功能**：文本向量化，用于知识库 chunk 的语义检索

### 9.3 可靠性保障

- **model_selector**：根据场景和负载选择合适的模型
- **circuit_breaker**：熔断器模式，当 AI 服务异常时自动熔断，避免级联故障

---

## 10. 向量检索方案

系统采用 **pgvector**（PostgreSQL 扩展）作为向量存储和检索引擎，替代独立的 Milvus 集群：

**优势：**
- 架构简化：关系数据与向量数据统一在 PostgreSQL 中管理
- 运维成本低：减少一个独立中间件的部署和维护
- 事务一致性：关系查询和向量检索可以在同一个 SQL 中完成
- pgvector 原生支持 IVFFlat 和 HNSW 索引，满足性能需求

**使用方式：**
- `t_knowledge_chunk` 表中包含 pgvector 向量字段
- 通过 SQLAlchemy async session 执行向量相似度查询
- 支持余弦相似度、内积、L2 距离等度量方式

---

## 11. 项目结构

```
src/ragent/
├── main.py                          # 应用入口，create_app() 工厂函数
├── app/                             # 应用层
│   ├── router.py                    # 主路由 (~815行)
│   ├── auth_router.py               # 认证路由
│   ├── deps.py                      # 依赖注入 (CurrentUser, DbSession)
│   ├── rate_limit.py                # IP+Redis 滑动窗口限流中间件
│   └── middleware.py                # Trace/RequestContext/ExceptionHandler 中间件
├── common/                          # 公共层
│   ├── models.py                    # 数据模型 (577行, 17张表)
│   ├── snowflake.py                 # Snowflake ID 生成器
│   ├── safe_json.py                 # SafeJSONResponse (BigInt精度修复)
│   └── json_utils.py               # LargeIntJSONEncoder
├── infra/                           # 基础设施层
│   ├── database.py                  # asyncpg + SQLAlchemy async session
│   ├── cache.py                     # Redis 连接池
│   ├── auth.py                      # JWT签发/验证 + bcrypt密码哈希
│   └── ai/                          # AI 服务层
│       ├── llm_service.py           # LLM 调用服务
│       ├── embedding_service.py     # 文本向量化服务
│       ├── model_selector.py        # 模型选择器
│       └── circuit_breaker.py       # 熔断器
├── ingestion/                       # 文档摄入管线
│   ├── pipeline.py                  # 6节点管线引擎
│   ├── nodes.py                     # Fetcher/Parser/Enhancer/Chunker/Enricher/Indexer
│   ├── tasks.py                     # Celery 任务 + asyncpg chunk_count 回写
│   └── context.py                   # IngestionContext
└── config/                          # 配置
    └── settings.py                  # pydantic-settings, 环境变量/.env
```

---

## 12. 关键设计决策

### 12.1 为什么选择 pgvector 而非独立向量数据库（Milvus）

- **架构简洁**：一个 PostgreSQL 实例同时承载关系数据和向量数据，减少服务数量
- **事务一致**：文档元数据和 chunk 向量在同一数据库中，可以利用数据库事务保证一致性
- **运维友好**：减少独立向量数据库的部署、监控、备份成本
- **性能足够**：对于中等规模知识库（百万级 chunk），pgvector 的 HNSW 索引性能完全满足需求

### 12.2 为什么选择 Celery + Redis 而非 RocketMQ

- **Python 生态原生**：Celery 是 Python 最成熟的分布式任务队列框架
- **部署简单**：Redis 同时作为缓存和消息队列，不需要额外的消息中间件
- **多 DB 复用**：Redis 通过不同 DB 编号隔离缓存和队列职责

### 12.3 为什么选择 FastAPI 而非 Spring Boot

- **Python 全栈统一**：后端、AI 推理、数据处理使用同一语言，降低技术栈复杂度
- **异步原生**：FastAPI 基于 async/await，天然适配 IO 密集型的 RAG 场景
- **Pydantic 集成**：自动数据校验和 OpenAPI 文档生成
- **轻量高效**：相比 Spring Boot 的庞大生态，FastAPI 更加轻量灵活

### 12.4 BigInt 精度修复

- JavaScript 的 Number 类型最大安全整数为 2^53 - 1
- Snowflake ID 超出此范围，前端 JSON 解析会丢失精度
- 通过 `SafeJSONResponse` 和 `LargeIntJSONEncoder` 将大整数转为字符串传输

---

## 13. 监控体系

### 13.1 指标采集

- **Prometheus**（端口 9090）负责指标采集和存储
- 采集目标包括：FastAPI 请求指标、Celery 任务指标、Redis 指标、PostgreSQL 指标

### 13.2 可视化

- **Grafana**（端口 3000）负责监控数据可视化
- 预置面板：API 响应时间、错误率、Celery 任务队列深度、数据库连接池状态

---

## 14. 环境配置

系统配置通过 `config/settings.py` 管理，基于 **pydantic-settings**：

- 优先读取环境变量，回退到 `.env` 文件
- 所有配置项均有类型注解和默认值
- 配置项包括：数据库连接串、Redis URL、JWT 密钥、AI API Key、Celery Broker URL 等
