# Ragent Python 版 — 数据模型与存储设计

## 1. 存储引擎总览

```
┌───────────────────────────────────────────────────────────┐
│                     存储架构                                │
│                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │  PostgreSQL  │  │    Redis     │  │  Milvus/pgvector  │ │
│  │  关系数据    │  │  缓存/锁/队列│  │  向量存储         │ │
│  │              │  │              │  │                   │ │
│  │ 用户/会话    │  │ 限流排队ZSET │  │ 文档分块向量       │ │
│  │ 知识库/文档  │  │ 分布式锁     │  │ Embedding 索引    │ │
│  │ 分块/意图    │  │ 幂等去重     │  │ HNSW 近邻搜索     │ │
│  │ Trace记录   │  │ Snowflake ID │  │                   │ │
│  │ 入库任务     │  │ 会话缓存     │  │                   │ │
│  └──────────────┘  └──────────────┘  └──────────────────┘ │
│                                                            │
│  ┌──────────────┐                                        │
│  │  RabbitMQ    │                                        │
│  │  消息持久化   │                                        │
│  │  入库任务     │                                        │
│  │  反馈消息     │                                        │
│  └──────────────┘                                        │
└───────────────────────────────────────────────────────────┘
```

---

## 2. 数据库 ER 关系

### 2.1 用户与会话域

```
┌───────────────────────────────────────────────────────────────┐
│                       用户与会话域                               │
├───────────────┬─────────────────┬──────────────────────────────┤
│  t_user       │ t_conversation  │ t_message                    │
│  用户表        │ 会话表           │ 消息表                       │
│               │                 │                              │
│  id           │ id              │ id                           │
│  username     │ user_id         │ conversation_id              │
│  password_hash│ title           │ user_id                      │
│  role         │ last_message_time│ role (user/assistant)       │
│  avatar       │                 │ content                      │
│  created_at   │                 │ thinking_content             │
│               │                 │ thinking_duration            │
│               │                 │ created_at                   │
├───────────────┼─────────────────┼──────────────────────────────┤
│               │ t_conversation_summary                        │
│               │ 会话摘要表                                     │
│               │                                               │
│               │ id                                            │
│               │ conversation_id                               │
│               │ user_id                                       │
│               │ content (摘要文本)                              │
│               │ last_message_id                               │
├───────────────┼───────────────────────────────────────────────┤
│               │ t_message_feedback                            │
│               │ 消息反馈表                                     │
│               │                                               │
│               │ id / message_id / user_id                     │
│               │ rating (like/dislike)                         │
│               │ comment                                       │
└───────────────┴───────────────────────────────────────────────┘
```

### 2.2 知识库域

```
┌───────────────────────────────────────────────────────────────────┐
│                         知识库域                                    │
├────────────────┬──────────────────┬──────────────┬────────────────┤
│ t_knowledge_   │ t_knowledge_     │ t_knowledge_ │ t_knowledge_   │
│  base          │  document        │  chunk       │  document_     │
│ 知识库表        │ 文档表            │ 分块表        │  chunk_log    │
│                │                  │              │ 分块日志表      │
│ id             │ id               │ id           │ id             │
│ name           │ kb_id            │ kb_id        │ doc_id         │
│ description    │ doc_name         │ doc_id       │ status         │
│ embedding_     │ file_url         │ chunk_index  │ extract_ms     │
│  model         │ file_type        │ content      │ chunk_ms       │
│ collection_    │ enabled          │ content_hash │ vectorize_ms   │
│  name          │ chunk_count      │ char_count   │ persist_ms     │
│ created_at     │ chunk_strategy   │ token_count  │ chunk_count    │
│                │ pipeline_id      │ keywords     │ error_message  │
│                │ process_mode     │ summary      │                │
│                │ created_at       │ enabled      │                │
└────────────────┴──────────────────┴──────────────┴────────────────┘
```

### 2.3 RAG 意图与检索域

```
┌─────────────────────────────────────────────────────────────────┐
│                    RAG 意图与检索域                                │
├──────────────────────┬──────────────────────────────────────────┤
│ t_intent_node        │ t_query_term_mapping                     │
│ 意图树节点表          │ 关键词归一化映射表                         │
│                      │                                          │
│ id                   │ id                                       │
│ kb_id                │ domain                                   │
│ intent_code          │ source_term                              │
│ name                 │ target_term                              │
│ level (0/1/2)        │ match_type (exact/fuzzy/prefix)          │
│ parent_code          │ priority                                 │
│ examples             │ enabled                                  │
│ collection_name      │                                          │
│ kind (0:RAG/1:TOOL)  │                                          │
│ system_prompt        │                                          │
│ rag_prompt           │                                          │
├──────────────────────┼──────────────────────────────────────────┤
│ t_rag_trace_run      │ t_rag_trace_node                         │
│ Trace 运行表          │ Trace 节点记录表                           │
│                      │                                          │
│ trace_id             │ trace_id                                 │
│ trace_name           │ node_id / parent_node_id                 │
│ conversation_id      │ depth                                    │
│ task_id              │ node_type / node_name                    │
│ status               │ module_name / function_name              │
│ duration_ms          │ status / duration_ms                     │
│ error_message        │ extra_data (JSON)                        │
└──────────────────────┴──────────────────────────────────────────┘
```

### 2.4 入库流水线域

```
┌─────────────────────────────────────────────────────────────────┐
│                       入库流水线域                                 │
├─────────────────┬──────────────────┬──────────────┬────────────┤
│ t_ingestion_    │ t_ingestion_     │ t_ingestion_ │t_ingestion_│
│  pipeline       │  pipeline_node   │  task        │  task_node │
│ 流水线表         │ 流水线节点表      │ 任务表        │ 任务节点表  │
│                 │                  │              │            │
│ id              │ id               │ id           │ id         │
│ name            │ pipeline_id      │ pipeline_id  │ task_id    │
│ description     │ node_id          │ source_type  │ pipeline_id│
│                 │ node_type        │ source_loc   │ node_id    │
│                 │ next_node_id     │ status       │ node_type  │
│                 │ settings_json    │ chunk_count  │ status     │
│                 │ condition_json   │ error_message│ duration_ms│
│                 │                  │ metadata_json│ output_json│
└─────────────────┴──────────────────┴──────────────┴────────────┘
```

---

## 3. 向量存储

### 3.1 双引擎支持

```
┌─────────────────────────────────────────────────────────┐
│              VectorStore 抽象接口                         │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌───────────────────┐    ┌──────────────────────────┐ │
│  │  Milvus 引擎       │    │  pgvector 引擎            │ │
│  │                    │    │                           │ │
│  │  优势:             │    │  优势:                    │ │
│  │  ├── 高性能检索    │    │  ├── 无需额外组件         │ │
│  │  ├── 百万级向量    │    │  ├── 部署简单             │ │
│  │  └── 专业向量数据库│    │  └── 适合小规模场景       │ │
│  │                    │    │                           │ │
│  │  Collection 结构:  │    │  表结构:                  │ │
│  │  ├── id (主键)     │    │  ├── id (主键)            │ │
│  │  ├── content (文本)│    │  ├── content (文本)       │ │
│  │  ├── embedding     │    │  ├── metadata (JSONB)     │ │
│  │  └── metadata      │    │  ├── embedding (vector)   │ │
│  │                    │    │  └── HNSW 索引            │ │
│  └───────────────────┘    └──────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## 4. ID 生成策略

所有业务表主键采用 Snowflake 分布式 ID 算法：

```
┌───┬───────────────┬──────────────┬──────────────┐
│ 1 │     41 bit     │    10 bit    │    12 bit    │
│符号│   时间戳(ms)   │  worker_id  │   序列号      │
└───┴───────────────┴──────────────┴──────────────┘

特点:
├── 趋势递增
├── 全局唯一
├── 包含时间信息（可反推生成时间）
└── worker_id 通过 Redis Lua 脚本在启动时原子分配
```

---

## 5. 数据库迁移

使用 Alembic 管理 PostgreSQL 的 Schema 版本：

```
┌───────────────────────────────────────────┐
│  Alembic 迁移管理                          │
│                                            │
│  versions/                                 │
│  ├── 001_create_user_tables.py            │
│  ├── 002_create_knowledge_tables.py       │
│  ├── 003_create_intent_tables.py          │
│  ├── 004_create_ingestion_tables.py       │
│  ├── 005_create_trace_tables.py           │
│  └── 006_create_pgvector_extension.py     │
│                                            │
│  操作:                                     │
│  ├── upgrade    — 升级到最新版本           │
│  ├── downgrade  — 回退到指定版本           │
│  └── stamp      — 标记当前版本             │
└───────────────────────────────────────────┘
```

---

## 6. Redis 使用场景汇总

| 场景             | 数据结构  | Key 格式                      | 说明                     |
|-----------------|----------|-------------------------------|--------------------------|
| 限流排队         | ZSET     | ragent:queue:{user_id}        | 按时间戳排序的请求队列    |
| 分布式锁         | STRING   | ragent:lock:{resource}        | NX + PX 原子锁           |
| 幂等去重         | STRING   | ragent:idempotent:{msg_id}    | SETNX + TTL              |
| Snowflake ID    | STRING   | ragent:snowflake:worker_id    | Lua 原子自增              |
| 会话缓存         | HASH     | ragent:session:{session_id}   | 对话上下文缓存            |
| 模型熔断状态     | HASH     | ragent:breaker:{model_id}     | 熔断器状态存储            |
| 排队通知         | PUB/SUB  | ragent:notify:{user_id}       | 释放许可时广播            |
