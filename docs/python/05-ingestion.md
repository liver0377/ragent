# Ragent Python 版 — 文档入库管线 (ingestion)

## 1. 模块定位

ingestion 模块是 Ragent 的**入库管线引擎**，负责将用户上传的文档经过完整的处理流水线——解析、增强、分块、丰富、向量化——最终写入向量数据库（pgvector），使文档可被语义检索。

**核心设计原则：所有入库操作均通过 Celery + Redis 异步执行，API 接口立即返回，不提供同步入库路线。**

### 模块文件结构

- **`context.py`** — `IngestionContext` 上下文数据容器 + `ChunkData` 分块数据类
- **`pipeline.py`** — `IngestionPipeline` 管线执行引擎，编排节点链式执行
- **`nodes.py`** — 6 个管线节点实现 + `NODE_REGISTRY` 注册表
- **`tasks.py`** — Celery 任务定义（`run_ingestion_pipeline`），含 `chunk_count` 回写

---

## 2. 入库总览

```
用户上传文档
     │
     ▼
┌──────────────┐     ┌──────────────┐     ┌─────────────────────────────────────┐
│  FastAPI 接口 │────▶│  Celery      │────▶│  Celery Worker                      │
│  接收文件     │     │  .delay()    │     │  (入库 Pipeline 引擎)                │
│  返回 task_id │     │  → Redis     │     │                                     │
│              │     │   Broker     │     │                                     │
└──────────────┘     └──────────────┘     └──────────────┬──────────────────────┘
                                                           │
                                          ┌────────────────┘
                                          │
             ┌────────────────────────────┼────────────────────────────┐
             ▼                            ▼                            ▼
    ┌──────────────┐           ┌──────────────┐           ┌──────────────┐
    │  Fetcher     │──────────▶│  Parser      │──────────▶│  Enhancer    │
    │  文件获取     │           │  文档解析     │           │  文本增强     │
    └──────────────┘           └──────────────┘           └──────┬───────┘
                                                                 │
             ┌───────────────────────────────────────────────────┘
             ▼                            ▼                            ▼
    ┌──────────────┐           ┌──────────────┐           ┌──────────────┐
    │  Chunker     │──────────▶│  Enricher    │──────────▶│  Indexer     │
    │  文本分块     │           │  分块增强     │           │  向量入库     │
    └──────────────┘           │  关键词/摘要  │           │  pgvector    │
                               └──────────────┘           └──────────────┘
```

**技术栈**：Celery 作为分布式任务队列，Redis 兼任 **Broker**（任务分发）和 **Result Backend**（结果存储），Celery Worker 进程执行管线。

---

## 3. Celery 异步入库流程

### 3.1 任务提交阶段

API 接口通过 `run_ingestion_pipeline.delay()` 将任务投递到 Redis Broker：

```python
from ragent.ingestion.tasks import run_ingestion_pipeline

celery_result = run_ingestion_pipeline.delay(
    task_id=task_id,              # Snowflake 任务 ID
    pipeline_id=knowledge_base_id, # 管线配置 ID（当前复用知识库 ID）
    source_type="local",          # 来源类型：local / http / s3
    source_location=file_path,    # 文件完整路径，如 /data/pdfs/3046_xxx.pdf
)
# celery_result.id → Celery 内部任务 ID（UUID），可用于状态查询
```

调用后立即返回，用户可通过 `GET /ingestion/tasks/{task_id}` 轮询任务进度。

### 3.2 Worker 执行阶段

Celery Worker 收到任务后，启动管线执行引擎：

```
┌──────────────────────────────────────────────────────────────────┐
│                    Celery Worker 进程                              │
│                                                                   │
│  1. run_ingestion_pipeline() 被 Worker 调用                       │
│  2. 构建 DEFAULT_PIPELINE_NODES（6 节点链）                        │
│  3. 创建 IngestionContext(task_id, pipeline_id, source_type, ...) │
│  4. asyncio.run(_run_pipeline_and_update())                       │
│     ├── pipeline.execute(ctx)  —  链式执行 6 个节点               │
│     └── _update_doc_chunk_count_async() — 回写 chunk_count       │
│  5. 返回序列化后的 IngestionContext 字典                           │
└──────────────────────────────────────────────────────────────────┘
```

**关键设计**：Celery 任务本身是**同步函数**（`def` 而非 `async def`），通过 `asyncio.run()` 在内部驱动异步管线。

**Celery 任务配置参数**：

- **`bind=True`** — 任务函数第一个参数为任务实例 `self`，可调用 `self.update_state()`
- **`name="ragent.ingestion.run_pipeline"`** — 任务注册名称
- **`queue="ingestion.task"`** — 专用队列，避免与其他类型任务混用
- **`acks_late=True`** — 任务执行完毕后才确认（而非 Worker 接收时确认），Worker 崩溃后任务可被重新分配
- **`track_started=True`** — 记录任务启动状态，使 `PROCESSING` 状态可被查询
- **`time_limit=600`** — 硬超时 10 分钟，超时后 Worker 强制终止任务
- **`soft_time_limit=540`** — 软超时 9 分钟，超时后抛出 `SoftTimeLimitExceeded` 异常，允许任务捕获后做清理

```python
@celery_app.task(
    bind=True,
    name="ragent.ingestion.run_pipeline",
    queue=INGESTION_TASK_QUEUE,  # "ingestion.task"
    acks_late=True,
    track_started=True,
    time_limit=600,       # 硬超时 10 分钟
    soft_time_limit=540,  # 软超时 9 分钟
)
def run_ingestion_pipeline(self, task_id, pipeline_id, source_type, source_location, ...):
    # 更新 Celery 状态为 PROCESSING
    self.update_state(state="PROCESSING", meta={"stage": "管线执行中", "progress": 20})

    # asyncio.run 驱动异步管线
    ctx = asyncio.run(_run_pipeline_and_update())
    return _serialize_context(ctx)
```

### 3.3 chunk_count 回写

管线执行完毕后，在同一个 `asyncio.run()` 协程内回写分块数量：

```python
async def _run_pipeline_and_update() -> None:
    ctx = await pipeline.execute(ctx)

    # 回写 chunk_count 到 t_knowledge_document 表
    chunk_count = len(ctx.chunks)
    if chunk_count > 0:
        await _update_doc_chunk_count_async(source_location, chunk_count)
```

### 3.4 状态追踪

通过 `AsyncResult` 查询任务状态：

```
PENDING ──▶ PROCESSING ──┬──▶ SUCCESS
                         │
                         └──▶ FAILURE
```

- **`PENDING`** — 任务已提交，等待 Worker 消费。`delay()` 调用后即进入此状态。
- **`PROCESSING`** — 管线执行中。Worker 开始执行时通过 `self.update_state()` 设置。
- **`SUCCESS`** — 入库完成。`return _serialize_context(ctx)` 后进入此状态。
- **`FAILURE`** — 入库失败。任务抛出异常后进入此状态。

查询接口 `GET /ingestion/tasks/{task_id}` 使用 `AsyncResult(task_id, app=celery_app)` 读取状态。

---

## 4. 管线节点详解

管线由 6 个节点组成，通过 `next_node_id` 串联为链表结构。所有节点继承自 `IngestionNode` 抽象基类，实现 `execute(ctx, settings)` 接口。

**默认节点链**（定义于 `tasks.py` 的 `DEFAULT_PIPELINE_NODES`）：

```
fetcher → parser → enhancer → chunker → enricher → indexer
```

### 4.1 FetcherNode — 文件获取

从数据源读取文件原始字节到上下文。

- **输入**：`source_type` + `source_location`
- **输出**：`ctx.raw_bytes`（原始字节）、`ctx.file_type`（根据扩展名检测）、`ctx.metadata["file_size"]`

当前支持 `local` 来源类型（本地文件系统）。根据文件扩展名映射文件类型：

- **`.pdf`** → `pdf`
- **`.docx` / `.doc`** → `docx`
- **`.md` / `.markdown`** → `md`
- **`.txt` / `.text` / `.csv`** → `txt`

### 4.2 ParserNode — 文档解析

将原始字节解析为纯文本，根据 `file_type` 选择解析器。

- **输入**：`ctx.raw_bytes` + `ctx.file_type`
- **输出**：`ctx.plain_text`（纯文本）、`ctx.metadata["text_length"]`

解析方式：

- **`pdf`** — `pdfplumber` 逐页提取文本（依赖 `pdfplumber` 库）
- **`docx`** — `python-docx` 提取段落文本（依赖 `python-docx` 库）
- **`md` / `txt`** — 直接 UTF-8 解码，后备 `latin-1`（无额外依赖）

### 4.3 EnhancerNode — 文本增强

对解析后的纯文本进行文档级别的增强处理（可选 LLM 增强）。

- **输入**：`ctx.plain_text`
- **输出**：`ctx.keywords`（关键词列表）

**两种模式**：

1. **LLM 增强**：当注入 `llm_service` 时，截取前 2000 字符调用 LLM 提取关键词
2. **基础增强**：无 LLM 时，基于正则分词 + 词频统计提取 Top 10 关键词

LLM 调用失败时自动降级为基础增强。

### 4.4 ChunkerNode — 文本分块

将长文本切分为固定大小的块，支持两种策略。

- **输入**：`ctx.enhanced_text` 或 `ctx.plain_text`
- **输出**：`ctx.chunks[]`（`ChunkData` 列表）

**分块策略**：

- **`fixed`（默认）** — 按字符数切分，支持重叠。配置参数：`chunk_size=500`, `overlap=50`
- **`structure`** — 按 Markdown 标题（`#` ~ `######`）切分，无标题时回退到 `fixed`

每个 `ChunkData` 自动计算：

- **`char_count`** — 字符数
- **`token_count`** — 近似 token 数（`len(content) // 4`）
- **`content_hash`** — SHA-256 哈希摘要

### 4.5 EnricherNode — 分块增强

对每个分块进行二次增强，提取块级关键词和摘要（可选 LLM 增强）。

- **输入**：`ctx.chunks[]`
- **输出**：每个 `ChunkData` 的 `keywords` + `summary`

**两种模式**：

1. **LLM 增强**：截取分块前 500 字符，调用 LLM 提取 3-5 个关键词和一句话摘要
2. **基础增强**：截取分块前 100 字符作为摘要

### 4.6 IndexerNode — 向量化 + 存储

将分块数据写入向量存储。

- **输入**：`ctx.chunks[]`
- **输出**：每个 `ChunkData` 的 `vector`（向量嵌入）+ `ctx.metadata["indexed_chunks"]`

**当前实现**：若注入 `embedding_service`，调用 `embed_batch()` 对分块进行向量化；存储层为 Mock 实现，预留 pgvector 集成接口。

### 4.7 节点注册表

所有节点通过 `NODE_REGISTRY` 注册，按 `node_type` 字符串查找：

```python
NODE_REGISTRY: dict[str, type[IngestionNode]] = {
    "fetcher":  FetcherNode,
    "parser":   ParserNode,
    "enhancer": EnhancerNode,
    "chunker":  ChunkerNode,
    "enricher": EnricherNode,
    "indexer":  IndexerNode,
}
```

`get_node(node_type)` 工厂函数根据类型名创建节点实例。

### 4.8 条件执行

每个节点支持通过 `condition_json` 配置条件跳过：

- **`"file_type": "pdf"`** — 仅处理 PDF 文件
- **`"source_type": ["local", "http"]`** — 仅处理指定来源类型

节点可通过重写 `should_execute()` 方法实现自定义条件逻辑。

---

## 5. 文档管理 API

### 5.1 文档列表

```
GET /api/v1/knowledge-bases/{kb_id}/documents
```

查询指定知识库下的文档列表，支持分页。

**查询参数**：

- **`page`**（int，默认 1）— 页码（≥ 1）
- **`page_size`**（int，默认 20）— 每页数量（1-100）

**响应字段**：`id`、`doc_name`、`file_type`、`enabled`、`chunk_count`、`chunk_strategy`、`process_mode`、`created_at`、`updated_at`

需认证 + 部门隔离（用户只能访问所属部门的知识库文档）。

### 5.2 删除文档

```
DELETE /api/v1/documents/{doc_id}
```

删除单个文档及其关联的分块记录。

**权限校验流程**：

1. 查询文档记录，不存在返回 404
2. 通过文档的 `kb_id` 关联查询所属知识库
3. 调用 `_check_kb_dept_access()` 进行部门权限检查：admin 可访问所有；普通用户只能操作本部门或未分配部门的知识库文档
4. 权限不匹配返回 403（部门隔离）
5. 通过后执行 `db.delete(doc)` 删除文档记录

需认证。

### 5.3 查询入库任务状态

```
GET /api/v1/ingestion/tasks/{task_id}
```

通过 Celery `AsyncResult` 查询任务执行状态。各状态返回内容：

- **`PENDING`** — 返回 `task_id`、`status`、`message`（"任务等待执行"）
- **`PROCESSING`** — 返回 `task_id`、`status`、`stage`（当前阶段描述）、`progress`（进度百分比）
- **`SUCCESS`** — 返回 `task_id`、`status`（从管线上下文提取）、`chunk_count`、`file_type`、`text_length`、`keywords`、`elapsed_ms`、`error_message`
- **`FAILURE`** — 返回错误码 500 及失败原因描述

该接口无需认证，通过 `task_id`（Celery UUID）直接查询。

---

## 6. 文件上传设计

### 6.1 上传接口

```
POST /api/v1/documents/upload
Content-Type: multipart/form-data
```

**请求参数**：

- **`knowledge_base_id`**（Form int）— 目标知识库 ID
- **`files`**（list[UploadFile]）— 上传的文件（支持多文件批量上传）

**需认证**。

### 6.2 完整处理流程

上传接口在处理文件之前，先进行两级前置校验（知识库级别），然后逐文件处理：

```
请求进入 upload_document()
   │
   ├── 前置校验（知识库级别，仅一次）
   │   │
   │   ├── 1. 验证知识库存在
   │   │      SELECT KnowledgeBase WHERE id = kb_id
   │   │      不存在 → 404
   │   │
   │   └── 2. 部门权限检查
   │          _check_kb_dept_access(kb, current_user)
   │          admin → 放行
   │          未分配部门 → 放行
   │          非本部门 → 403
   │
   ├── 确保存储目录 /data/pdfs 存在（os.makedirs）
   │
   └── 逐文件处理（每个文件独立流程）
       │
       ├── 3. 检查文件类型（白名单）
       │      允许的扩展名：pdf / txt / md / docx / doc / csv / xlsx / json
       │      不匹配 → status=REJECTED, 跳过该文件
       │
       ├── 4. 同名文档去重检查（kb_id + doc_name）
       │      SELECT KnowledgeDocument WHERE kb_id=? AND doc_name=?
       │      已存在 → status=SKIPPED, 跳过该文件
       │
       ├── 5. 生成 doc_id（Snowflake ID）
       │
       ├── 6. 保存文件到 /data/pdfs/{doc_id}_{filename}
       │      doc_id 前缀确保不同知识库上传同名文件不会互相覆盖
       │
       ├── 7. 创建数据库记录（KnowledgeDocument）
       │      chunk_count=0, chunk_strategy="fixed", process_mode="auto"
       │      file_url="/data/pdfs/{doc_id}_{filename}"
       │
       └── 8. 提交 Celery 异步任务
              run_ingestion_pipeline.delay(
                  task_id=<新 Snowflake ID>,
                  pipeline_id=knowledge_base_id,
                  source_type="local",
                  source_location=file_path,
              )
              返回 task_id + celery_task_id
```

### 6.3 多文件批量上传

单次请求支持多个文件，**每个文件独立创建一条文档记录和独立的 Celery 任务**：

- 每个文件有独立的 `doc_id`（Snowflake ID）
- 每个文件有独立的 `task_id` 和 `celery_task_id`
- 单个文件的校验失败（类型不符/重复）不影响其他文件
- 最终返回汇总结果：`total`（总文件数）、`success`（成功提交数）、`failed`（失败数）、`details`（每个文件的详细状态）

响应示例：

```json
{
  "code": 200,
  "data": {
    "total": 3,
    "success": 2,
    "failed": 1,
    "details": [
      { "filename": "报告.pdf", "doc_id": 3046, "task_id": 4001, "status": "PENDING", "message": "..." },
      { "filename": "旧文档.pdf", "status": "SKIPPED", "message": "文档已存在，跳过重复上传" },
      { "filename": "readme.docx", "doc_id": 3047, "task_id": 4002, "status": "PENDING", "message": "..." }
    ]
  }
}
```

### 6.4 文件存储与持久化

**存储路径**：`/data/pdfs/{doc_id}_{filename}`

- `doc_id` 前缀确保即使不同用户上传同名文件也不会互相覆盖
- 数据库 `file_url` 字段保存完整路径，Celery Worker 的 FetcherNode 据此定位文件

**命名卷持久化**：在 `docker-compose.yml` 中，`upload-data` 命名卷（named volume）挂载到容器内 `/data/pdfs` 路径。API 服务和 Celery Worker 容器挂载**同一卷**，确保：

- API 服务写入的文件，Celery Worker 可以读取
- 容器重启后上传文件不丢失（named volume 生命周期独立于容器）
- 多 Worker 实例可共享同一存储

```
# docker-compose.yml 示意
volumes:
  upload-data:          # 命名卷定义

services:
  api:
    volumes:
      - upload-data:/data/pdfs

  celery-worker:
    volumes:
      - upload-data:/data/pdfs
```

### 6.5 允许的文件类型

上传接口维护文件扩展名白名单，不在白名单内的文件直接返回 `REJECTED`：

- **`pdf`** — PDF 文档
- **`txt`** — 纯文本
- **`md`** — Markdown 文档
- **`docx` / `doc`** — Word 文档
- **`csv`** — CSV 表格
- **`xlsx`** — Excel 表格
- **`json`** — JSON 文件

### 6.6 上传去重机制

每个文件在保存前执行去重检查，防止同一知识库内重复上传同名文档：

- **去重键**：`kb_id` + `doc_name` 联合匹配
- **查询方式**：`SELECT KnowledgeDocument WHERE kb_id = ? AND doc_name = ?`
- **命中时行为**：跳过该文件，返回 `status=SKIPPED`，不创建记录、不保存文件、不提交任务
- **去重范围**：仅在同一知识库内去重，不同知识库可以上传同名文件（文件通过 `doc_id` 前缀隔离存储）

---

## 7. chunk_count 回写机制

### 7.1 为什么使用 asyncpg 而非 ORM

管线执行完毕后，需要将分块数量 `chunk_count` 回写到 `t_knowledge_document` 表。此操作使用 `asyncpg` 直接 SQL 而非 SQLAlchemy ORM，原因如下：

1. **Celery 同步上下文**：`run_ingestion_pipeline` 是同步函数（`def`），通过 `asyncio.run()` 创建新的事件循环来驱动管线。SQLAlchemy 的异步会话（`AsyncSession`）依赖 FastAPI 的请求级生命周期，在 Celery Worker 中不可用。

2. **事件循环冲突**：`asyncio.run()` 会创建一个新的事件循环。若在循环内尝试通过 ORM 创建新的数据库会话，可能遇到「已有运行中的事件循环」冲突。

3. **轻量直接**：`chunk_count` 回写是一条简单的 `UPDATE` 语句，无需 ORM 的复杂查询构建能力。`asyncpg` 作为轻量级异步 PostgreSQL 驱动，在已有事件循环中直接连接数据库更简洁可靠。

### 7.2 实现细节

```python
async def _update_doc_chunk_count_async(source_location: str, chunk_count: int) -> None:
    import asyncpg

    settings = get_settings()
    db_url = settings.DATABASE_URL

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            "UPDATE t_knowledge_document SET chunk_count = $1 WHERE file_url = $2",
            chunk_count, source_location,
        )
    finally:
        await conn.close()
```

**关键设计点**：

- **匹配字段**：通过 `file_url`（即 `source_location`，如 `/data/pdfs/304629849111134208_报告.pdf`）定位文档记录，而非 doc_id。这是因为 Celery 任务参数中传递的是 `source_location`，无需额外查询 doc_id。
- **连接管理**：每次回写创建独立的 `asyncpg` 连接，执行完毕后立即关闭（`finally` 块确保连接释放）。
- **容错设计**：回写失败仅记录警告日志（`logger.warning`），不抛出异常，不影响管线整体返回结果。即使回写失败，文档分块数据仍然正常写入向量库。
- **执行时序**：在 `pipeline.execute(ctx)` 之后、`asyncio.run()` 返回之前执行，复用同一事件循环，避免创建额外循环。

### 7.3 数据流转示意

```
上传接口                     Celery Worker                     PostgreSQL
   │                              │                                │
   │ 创建记录                      │                                │
   │ chunk_count=0 ──────────────────────────────────────────────▶│ INSERT
   │                              │                                │
   │ 提交任务 ────────────────▶   │                                │
   │                              │ pipeline.execute(ctx)          │
   │                              │ → 6 节点处理                   │
   │                              │ → ctx.chunks 生成              │
   │                              │                                │
   │                              │ _update_doc_chunk_count_async  │
   │                              │ chunk_count=len(ctx.chunks)    │
   │                              │ ──────────────────────────────▶│ UPDATE
   │                              │                                │ chunk_count=N
```

---

## 8. 设计原则

- **全异步**：所有入库操作通过 Celery + Redis 异步执行，API 接口立即返回 `task_id`
- **可追溯**：通过 Celery `AsyncResult` + `track_started=True` 实时追踪任务状态（PENDING → PROCESSING → SUCCESS/FAILURE）
- **可恢复**：`acks_late=True` 确保任务执行完毕才确认，Worker 崩溃后任务可被重新分配
- **可插拔**：Pipeline 节点通过 `NODE_REGISTRY` 注册 + `NodeConfig` 配置组合，新增节点无需修改引擎
- **条件执行**：每个节点支持 `condition_json` 条件过滤（`file_type`、`source_type`），按需跳过
- **优雅降级**：EnhancerNode / EnricherNode 在 LLM 不可用时自动降级为基础算法
- **超时保护**：Celery 任务硬超时 10 分钟、软超时 9 分钟，防止任务无限阻塞
- **限流保护**：上传接口配置限流规则（60 秒内最多 10 次请求），防止大量上传冲击系统
- **文件持久化**：Docker named volume 确保 API 服务和 Worker 共享存储，容器重启不丢失文件
- **上传去重**：基于 `kb_id` + `doc_name` 联合去重，避免重复入库同一文档
- **数据回写**：管线完成后通过 asyncpg 轻量回写 `chunk_count`，与 ORM 生命周期解耦
