# Ragent Python 版 — API 参考文档

本文档列出 Ragent 平台全部 **18 个 API 端点**，涵盖认证、RAG 问答、知识库管理、文档入库、会话管理和部门管理等核心功能。

---

## 1. 通用说明

### 1.1 基础信息

- **Base URL**：`http://{host}:{port}/api/v1`
- **协议**：HTTP / HTTPS（生产环境建议 HTTPS）
- **数据格式**：JSON（`Content-Type: application/json`），文件上传除外
- **字符编码**：UTF-8
- **认证方式**：JWT Bearer Token（除 `health`、`register`、`login` 外均需认证）

### 1.2 统一响应格式

所有接口（除 SSE 流和健康检查外）返回统一的 `Result` 结构：

```
{
  "code": 0,           // 0 表示成功，非零表示错误
  "message": "success", // 状态描述
  "data": { ... },     // 业务数据（成功时有值）
  "trace_id": null,    // 分布式追踪 ID（错误时有值）
  "timestamp": 1713628800.0  // 响应时间戳（Unix 时间戳）
}
```

**常见错误码：**

- **code: 0** — 成功
- **code: 404** — 资源不存在
- **code: 403** — 权限不足（部门隔离）
- **HTTP 401** — 未认证 / Token 无效 / Token 过期
- **HTTP 409** — 资源冲突（如用户名已注册）
- **HTTP 429** — 请求频率超限（触发限流）

### 1.3 认证方式

需要认证的端点须在请求头中携带 JWT Token：

```
Authorization: Bearer <access_token>
```

Token 通过注册或登录接口获取，包含 `sub`（用户 ID）声明。

**认证失败响应：**

- **未提供凭据**：`HTTP 401`，`"未提供认证凭据"`
- **Token 过期**：`HTTP 401`，`"Token 已过期，请重新登录"`
- **Token 无效**：`HTTP 401`，`"Token 无效"`
- **用户不存在**：`HTTP 401`，`"用户不存在"`

### 1.4 限流规则

系统基于 Redis 滑动窗口按 **IP + 路径前缀** 进行分布式限流：

- **POST /api/v1/auth/register** — **5 次/分钟**
- **POST /api/v1/auth/login** — **10 次/分钟**
- **POST /api/v1/chat** — **20 次/分钟**
- **POST /api/v1/documents/upload** — **10 次/分钟**

超限时返回 `HTTP 429 Too Many Requests`。

### 1.5 部门权限隔离

知识库和文档相关的端点遵循部门隔离规则：

- **admin 用户**：可访问所有知识库和文档
- **普通用户**：只能访问本部门（`department_id` 匹配）或未分配部门（`department_id` 为 null）的知识库和文档

创建知识库时，`department_id` 自动设置为当前用户的部门 ID。

---

## 2. 认证接口（prefix: /api/v1/auth）

### 2.1 POST /api/v1/auth/register — 用户注册

注册新用户，成功后自动返回 JWT Token。

**认证要求：** 无

**限流：** 5 次/分钟（按 IP）

**请求体（JSON）：**

- **username** `string` `必填` — 用户名，3~32 个字符
- **password** `string` `必填` — 密码，6~128 个字符
- **department_id** `int | null` `可选` — 所属部门 ID，默认 `null`

**请求示例：**

```json
{
  "username": "zhangsan",
  "password": "mypassword123",
  "department_id": 1
}
```

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "user": {
      "id": 1893456789012345678,
      "username": "zhangsan",
      "role": "user",
      "department_id": 1
    },
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer"
  },
  "timestamp": 1713628800.0
}
```

**错误响应：**

- **HTTP 409** — 用户名已被注册：`"用户名 'zhangsan' 已被注册"`

**流程说明：**

1. 检查用户名是否已存在
2. 生成 Snowflake ID
3. 使用 bcrypt 哈希密码
4. 写入数据库并生成 JWT Token

---

### 2.2 POST /api/v1/auth/login — 用户登录

用户名密码登录，返回 JWT Token。

**认证要求：** 无

**限流：** 10 次/分钟（按 IP）

**请求体（JSON）：**

- **username** `string` `必填` — 用户名，至少 1 个字符
- **password** `string` `必填` — 密码，至少 1 个字符

**请求示例：**

```json
{
  "username": "zhangsan",
  "password": "mypassword123"
}
```

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "user": {
      "id": 1893456789012345678,
      "username": "zhangsan",
      "role": "user",
      "avatar": null,
      "department_id": 1
    },
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer"
  },
  "timestamp": 1713628800.0
}
```

**错误响应：**

- **HTTP 401** — 用户名或密码错误：`"用户名或密码错误"`

---

### 2.3 GET /api/v1/auth/me — 获取当前用户信息

获取当前登录用户的详细信息，需 Bearer Token。

**认证要求：** Bearer Token（必须）

**限流：** 无特殊限制

**请求参数：** 无

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1893456789012345678,
    "username": "zhangsan",
    "role": "user",
    "avatar": null,
    "department_id": 1
  },
  "timestamp": 1713628800.0
}
```

---

## 3. 核心业务接口（prefix: /api/v1）

以下所有端点均需 **Bearer Token 认证**。

---

### 3.1 GET /api/v1/health — 健康检查

服务健康检查，返回运行状态和版本号。

**认证要求：** 无

**限流：** 无

**请求参数：** 无

**响应（200）：**

```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

> 注意：此端点不使用 `Result` 统一响应格式，直接返回原始 JSON。

---

### 3.2 POST /api/v1/chat — RAG 问答（SSE 流式响应）

发起 RAG 智能问答，通过 SSE（Server-Sent Events）流式返回结果。

**认证要求：** Bearer Token（必须）

**限流：** 20 次/分钟（按 IP）

**请求体（JSON）：**

- **question** `string` `必填` — 用户问题，至少 1 个字符
- **conversation_id** `int | null` `可选` — 会话 ID，传入则延续已有会话，默认 `null`
- **knowledge_base_id** `int | null` `可选` — 知识库 ID，指定检索范围，默认 `null`
- **user_id** `int | null` `可选` — 用户 ID（内部使用，通常由 Token 自动填充），默认 `null`

**请求示例：**

```json
{
  "question": "Ragent 项目的技术架构是什么？",
  "conversation_id": 1893456789012345678,
  "knowledge_base_id": 1893456789012345690
}
```

**响应格式：** `Content-Type: text/event-stream`

SSE 事件流，逐 token 推送。前端通过 `EventSource` 或 `fetch` + `ReadableStream` 实时读取。

**处理流程：**

1. JWT 认证校验
2. IP 限流检查
3. 初始化 `RAGChain`（含 LLM Service、Embedding Service）
4. 调用 `RAGChain.ask()` 生成 SSE 事件流
5. 返回 `StreamingResponse`（SSE 格式）

> 注意：此端点不返回 `Result` 统一响应格式，而是返回 SSE 事件流。

---

### 3.3 POST /api/v1/knowledge-bases — 创建知识库

创建新知识库，自动绑定当前用户的部门 ID。

**认证要求：** Bearer Token（必须）

**请求体（JSON）：**

- **name** `string` `必填` — 知识库名称，1~100 个字符
- **description** `string` `可选` — 知识库描述，最多 500 个字符，默认 `""`

**请求示例：**

```json
{
  "name": "产品文档库",
  "description": "存放所有产品相关技术文档"
}
```

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1893456789012345700,
    "name": "产品文档库",
    "description": "存放所有产品相关技术文档",
    "embedding_model": "Qwen/Qwen3-Embedding-8B",
    "collection_name": "kb_1893456789012345700",
    "department_id": 1,
    "created_at": "2025-04-20T12:00:00"
  },
  "timestamp": 1713628800.0
}
```

**流程说明：**

1. 生成 Snowflake ID
2. 自动生成 collection 名称 `kb_{id}`
3. 使用配置中的默认 Embedding 模型
4. 绑定当前用户的 `department_id`

---

### 3.4 GET /api/v1/knowledge-bases — 知识库列表

分页获取知识库列表，支持部门隔离。

**认证要求：** Bearer Token（必须）

**查询参数：**

- **page** `int` `可选` — 页码，从 1 开始，默认 `1`
- **page_size** `int` `可选` — 每页数量，1~100，默认 `20`

**部门隔离规则：**

- admin 用户：查看所有知识库
- 普通用户：仅查看本部门 + 未分配部门的知识库

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": 1893456789012345700,
        "name": "产品文档库",
        "description": "存放所有产品相关技术文档",
        "embedding_model": "Qwen/Qwen3-Embedding-8B",
        "collection_name": "kb_1893456789012345700",
        "department_id": 1,
        "created_at": "2025-04-20T12:00:00",
        "updated_at": "2025-04-20T13:00:00"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20
  },
  "timestamp": 1713628800.0
}
```

**排序规则：** 按 `created_at` 降序（最新创建的在前）

---

### 3.5 GET /api/v1/knowledge-bases/{kb_id} — 知识库详情

获取指定知识库的详细信息，包含关联的文档数量。

**认证要求：** Bearer Token（必须）

**路径参数：**

- **kb_id** `int` `必填` — 知识库 ID

**部门隔离：** 非 admin 用户只能查看本部门或未分配部门的知识库

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1893456789012345700,
    "name": "产品文档库",
    "description": "存放所有产品相关技术文档",
    "embedding_model": "Qwen/Qwen3-Embedding-8B",
    "collection_name": "kb_1893456789012345700",
    "department_id": 1,
    "document_count": 15,
    "created_at": "2025-04-20T12:00:00",
    "updated_at": "2025-04-20T13:00:00"
  },
  "timestamp": 1713628800.0
}
```

**错误响应：**

- **code: 404** — 知识库不存在
- **code: 403** — 无权访问（部门隔离）

> 注意：响应中包含 `document_count` 字段，为该知识库下的文档总数。

---

### 3.6 DELETE /api/v1/knowledge-bases/{kb_id} — 删除知识库

删除指定知识库及其关联的文档和分块记录。

**认证要求：** Bearer Token（必须）

**路径参数：**

- **kb_id** `int` `必填` — 知识库 ID

**部门隔离：** 非 admin 用户只能删除本部门或未分配部门的知识库

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "message": "知识库 '产品文档库' 已删除"
  },
  "timestamp": 1713628800.0
}
```

**错误响应：**

- **code: 404** — 知识库不存在
- **code: 403** — 无权删除（部门隔离）

---

### 3.7 POST /api/v1/documents/upload — 文档上传

批量上传文档到指定知识库，提交 Celery 异步摄入任务。使用 `multipart/form-data` 格式。

**认证要求：** Bearer Token（必须）

**限流：** 10 次/分钟（按 IP）

**请求格式：** `multipart/form-data`

**表单字段：**

- **knowledge_base_id** `int` `必填` — 目标知识库 ID
- **files** `File[]` `必填` — 上传的文件列表（支持多文件）

**支持的文件类型：** `pdf`、`txt`、`md`、`docx`、`doc`、`csv`、`xlsx`、`json`

**部门隔离：** 需有目标知识库的访问权限

**处理逻辑：**

1. 验证知识库存在且用户有权限
2. 逐个处理上传文件：
   - 检查文件类型是否受支持，不支持则标记 `REJECTED`
   - 检查同知识库下是否存在同名文档（去重），存在则标记 `SKIPPED`
   - 保存文件到 `/data/pdfs/` 目录（使用 `{doc_id}_{filename}` 命名避免覆盖）
   - 创建文档记录
   - 提交 Celery 异步摄入任务
   - 标记 `PENDING`

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 3,
    "success": 2,
    "failed": 1,
    "details": [
      {
        "filename": "report.pdf",
        "doc_id": 1893456789012345800,
        "task_id": 1893456789012345801,
        "celery_task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "status": "PENDING",
        "message": "文档 report.pdf 已提交摄入队列",
        "file_size": 1048576
      },
      {
        "filename": "notes.txt",
        "doc_id": 1893456789012345802,
        "task_id": 1893456789012345803,
        "celery_task_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "status": "PENDING",
        "message": "文档 notes.txt 已提交摄入队列",
        "file_size": 2048
      },
      {
        "filename": "image.png",
        "status": "REJECTED",
        "message": "不支持的文件类型: png"
      }
    ]
  },
  "timestamp": 1713628800.0
}
```

**错误响应：**

- **code: 404** — 目标知识库不存在
- **code: 403** — 无权向该知识库上传（部门隔离）

> 提示：可通过 `task_id` 或 `celery_task_id` 调用"查询摄入任务状态"接口跟踪处理进度。

---

### 3.8 GET /api/v1/knowledge-bases/{kb_id}/documents — 知识库文档列表

分页查询指定知识库下的文档列表。

**认证要求：** Bearer Token（必须）

**路径参数：**

- **kb_id** `int` `必填` — 知识库 ID

**查询参数：**

- **page** `int` `可选` — 页码，从 1 开始，默认 `1`
- **page_size** `int` `可选` — 每页数量，1~100，默认 `20`

**部门隔离：** 需有目标知识库的访问权限

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": 1893456789012345800,
        "doc_name": "report.pdf",
        "file_type": "pdf",
        "enabled": true,
        "chunk_count": 42,
        "chunk_strategy": "fixed",
        "process_mode": "auto",
        "created_at": "2025-04-20T14:00:00",
        "updated_at": "2025-04-20T14:05:00"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20
  },
  "timestamp": 1713628800.0
}
```

**排序规则：** 按 `created_at` 降序（最新上传的在前）

**错误响应：**

- **code: 404** — 知识库不存在
- **code: 403** — 无权访问（部门隔离）

---

### 3.9 DELETE /api/v1/documents/{doc_id} — 删除文档

删除指定文档及其关联的分块记录。

**认证要求：** Bearer Token（必须）

**路径参数：**

- **doc_id** `int` `必填` — 文档 ID

**部门隔离：** 通过文档所属知识库的部门 ID 进行权限检查

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "message": "文档 'report.pdf' 已删除"
  },
  "timestamp": 1713628800.0
}
```

**错误响应：**

- **code: 404** — 文档不存在
- **code: 403** — 无权删除（通过所属知识库的部门隔离检查）

---

### 3.10 GET /api/v1/ingestion/tasks/{task_id} — 查询摄入任务状态

查询文档摄入（入库）异步任务的执行状态。

**认证要求：** Bearer Token（必须）

**路径参数：**

- **task_id** `string` `必填` — 任务 ID（上传时返回的 `task_id` 或 `celery_task_id`）

**成功响应 — 等待中（PENDING）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "1893456789012345801",
    "status": "PENDING",
    "message": "任务等待执行"
  },
  "timestamp": 1713628800.0
}
```

**成功响应 — 处理中（PROCESSING）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "1893456789012345801",
    "status": "PROCESSING",
    "stage": "chunking",
    "progress": 65
  },
  "timestamp": 1713628800.0
}
```

**成功响应 — 已完成（SUCCESS）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "1893456789012345801",
    "status": "COMPLETED",
    "chunk_count": 42,
    "file_type": "pdf",
    "text_length": 15000,
    "keywords": ["技术架构", "FastAPI", "RAG"],
    "elapsed_ms": 3200,
    "error_message": null
  },
  "timestamp": 1713628800.0
}
```

**错误响应 — 任务失败（FAILURE）：**

```json
{
  "code": 500,
  "message": "任务执行失败: PDF 解析异常",
  "data": null,
  "timestamp": 1713628800.0
}
```

---

### 3.11 POST /api/v1/conversations — 创建会话

创建新的对话会话。

**认证要求：** Bearer Token（必须）

**请求体（JSON）：**

- **title** `string` `可选` — 会话标题，最多 200 个字符，默认 `"新对话"`

**请求示例：**

```json
{
  "title": "技术架构讨论"
}
```

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1893456789012345900,
    "title": "技术架构讨论",
    "user_id": 1893456789012345678,
    "created_at": "2025-04-20T15:00:00"
  },
  "timestamp": 1713628800.0
}
```

---

### 3.12 GET /api/v1/conversations — 会话列表

获取当前用户的会话列表，按最后消息时间倒序排列。

**认证要求：** Bearer Token（必须）

**查询参数：**

- **page** `int` `可选` — 页码，从 1 开始，默认 `1`
- **page_size** `int` `可选` — 每页数量，1~100，默认 `20`

**权限隔离：** 仅返回当前用户自己的会话

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": 1893456789012345900,
        "title": "技术架构讨论",
        "last_message_time": "2025-04-20T15:30:00",
        "created_at": "2025-04-20T15:00:00"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20
  },
  "timestamp": 1713628800.0
}
```

**排序规则：** 按 `last_message_time` 降序（最新活跃的在前），无消息时间的按 `created_at` 降序

---

### 3.13 GET /api/v1/conversations/{conv_id} — 会话详情

获取指定会话的详情及其所有消息列表。

**认证要求：** Bearer Token（必须）

**路径参数：**

- **conv_id** `int` `必填` — 会话 ID

**权限隔离：** 只能查看自己的会话

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1893456789012345900,
    "title": "技术架构讨论",
    "user_id": 1893456789012345678,
    "last_message_time": "2025-04-20T15:30:00",
    "created_at": "2025-04-20T15:00:00",
    "messages": [
      {
        "id": 1893456789012345901,
        "role": "user",
        "content": "Ragent 的技术架构是什么？",
        "created_at": "2025-04-20T15:10:00"
      },
      {
        "id": 1893456789012345902,
        "role": "assistant",
        "content": "Ragent 采用 FastAPI + Celery 技术栈...",
        "created_at": "2025-04-20T15:10:05"
      }
    ]
  },
  "timestamp": 1713628800.0
}
```

**错误响应：**

- **code: 404** — 会话不存在或无权访问

> 注意：消息按 `created_at` 升序排列（最早消息在前）。

---

### 3.14 DELETE /api/v1/conversations/{conv_id} — 删除会话

删除指定会话及其所有消息。

**认证要求：** Bearer Token（必须）

**路径参数：**

- **conv_id** `int` `必填` — 会话 ID

**权限隔离：** 只能删除自己的会话

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "message": "会话已删除"
  },
  "timestamp": 1713628800.0
}
```

**错误响应：**

- **code: 404** — 会话不存在或无权访问

---

### 3.15 GET /api/v1/departments — 部门列表

获取所有部门列表。

**认证要求：** Bearer Token（必须）

**请求参数：** 无

**成功响应（200）：**

```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "id": 1,
      "name": "技术部",
      "description": "负责技术研发"
    },
    {
      "id": 2,
      "name": "产品部",
      "description": "负责产品设计"
    }
  ],
  "timestamp": 1713628800.0
}
```

**排序规则：** 按 `id` 升序

> 注意：此端点的 `data` 直接是数组，不是分页结构。

---

## 4. 端点速查清单

以下按功能分类列出全部 18 个端点：

**认证（3 个）：**

1. `POST /api/v1/auth/register` — 用户注册
2. `POST /api/v1/auth/login` — 用户登录
3. `GET /api/v1/auth/me` — 获取当前用户信息 🔒

**系统（1 个）：**

4. `GET /api/v1/health` — 健康检查

**RAG 问答（1 个）：**

5. `POST /api/v1/chat` — RAG 问答（SSE 流式）🔒

**知识库管理（4 个）：**

6. `POST /api/v1/knowledge-bases` — 创建知识库 🔒
7. `GET /api/v1/knowledge-bases` — 知识库列表 🔒
8. `GET /api/v1/knowledge-bases/{kb_id}` — 知识库详情 🔒
9. `DELETE /api/v1/knowledge-bases/{kb_id}` — 删除知识库 🔒

**文档管理（3 个）：**

10. `POST /api/v1/documents/upload` — 文档上传 🔒
11. `GET /api/v1/knowledge-bases/{kb_id}/documents` — 知识库文档列表 🔒
12. `DELETE /api/v1/documents/{doc_id}` — 删除文档 🔒

**入库任务（1 个）：**

13. `GET /api/v1/ingestion/tasks/{task_id}` — 查询摄入任务状态 🔒

**会话管理（4 个）：**

14. `POST /api/v1/conversations` — 创建会话 🔒
15. `GET /api/v1/conversations` — 会话列表 🔒
16. `GET /api/v1/conversations/{conv_id}` — 会话详情 🔒
17. `DELETE /api/v1/conversations/{conv_id}` — 删除会话 🔒

**部门管理（1 个）：**

18. `GET /api/v1/departments` — 部门列表 🔒

> 🔒 标记表示该端点需要 Bearer Token 认证。

---

## 5. 分页参数约定

所有列表接口采用统一的分页参数：

- **page** — 页码，从 `1` 开始，默认 `1`
- **page_size** — 每页数量，范围 `1~100`，默认 `20`

分页响应结构：

```
{
  "items": [ ... ],     // 当前页数据列表
  "total": 100,         // 总记录数
  "page": 1,            // 当前页码
  "page_size": 20       // 每页数量
}
```

---

## 6. 错误处理约定

**HTTP 层错误（由框架/中间件返回）：**

- **400** — 请求参数验证失败（Pydantic 校验错误）
- **401** — 认证失败（Token 缺失/无效/过期）
- **429** — 请求频率超限（触发限流中间件）

**业务层错误（由接口逻辑返回，HTTP 200 + Result）：**

- **code: 404** — 资源不存在
- **code: 403** — 权限不足（部门隔离）
- **code: 500** — 内部错误（如任务执行失败）

---

## 7. 文件上传说明

文档上传端点（`POST /api/v1/documents/upload`）的特殊说明：

**Content-Type：** `multipart/form-data`（非 JSON）

**文件存储路径：** `/data/pdfs/{doc_id}_{filename}`

**Nginx 上传限制：** 100MB

**去重规则：** 同一知识库下同名文档视为重复，自动跳过

**异步处理：** 文件保存后立即返回，文档解析/分块/向量化通过 Celery 异步执行，可通过摄入任务状态接口查询进度
