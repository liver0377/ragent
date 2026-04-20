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

## 2. 全表清单与业务域划分

项目共 **18 张表**，分布在 5 个业务域中：

**用户与会话域（6 张表）**

- **t_department** — 部门表，企业组织架构，用于 RBAC 权限隔离
- **t_user** — 用户表，平台注册用户基本信息
- **t_conversation** — 会话表，用户与助手的对话会话
- **t_message** — 消息表，会话中的每条消息
- **t_conversation_summary** — 会话摘要表，长对话上下文压缩
- **t_message_feedback** — 消息反馈表，用户对助手回复的点赞/点踩

**知识库域（4 张表）**

- **t_knowledge_base** — 知识库表，管理知识库基本信息及 Embedding 模型
- **t_knowledge_document** — 知识文档表，上传到知识库的文档元数据
- **t_knowledge_chunk** — 知识分块表，文档切分后的文本分块
- **t_knowledge_document_chunk_log** — 文档分块日志表，处理各阶段的耗时与状态

**RAG 意图与检索域（2 张表）**

- **t_intent_node** — 意图树节点表，知识库的意图分类树结构
- **t_query_term_mapping** — 关键词归一化映射表，查询改写时的术语标准化

**Trace 域（2 张表）**

- **t_rag_trace_run** — Trace 运行表，RAG 请求的全链路追踪根节点
- **t_rag_trace_node** — Trace 节点记录表，链路中每个步骤的执行详情

**入库流水线域（4 张表）**

- **t_ingestion_pipeline** — 入库流水线表，文档处理流水线元信息
- **t_ingestion_pipeline_node** — 入库流水线节点表，流水线中处理节点的配置
- **t_ingestion_task** — 入库任务表，每条文档入库任务的执行状态
- **t_ingestion_task_node** — 入库任务节点表，任务中每个流水线节点的执行详情

---

## 3. 数据库 ER 关系

### 3.1 用户与会话域

```
┌───────────────────────────────────────────────────────────────────────────┐
│                       用户与会话域                                         │
├───────────────┬───────────────┬─────────────────┬────────────────────────┤
│ t_department  │ t_user        │ t_conversation  │ t_message              │
│ 部门表        │ 用户表         │ 会话表           │ 消息表                 │
│               │               │                 │                        │
│ id            │ id            │ id              │ id                     │
│ name          │ username      │ user_id (FK)    │ conversation_id (FK)   │
│ description   │ password_hash │ title           │ user_id (FK)           │
│               │ role          │ last_message_time│ role (user/assistant) │
│               │ avatar        │                 │ content                │
│               │ department_id │                 │ thinking_content       │
│               │   (FK→dept)   │                 │ thinking_duration      │
│               │ created_at    │                 │ created_at             │
│               │ updated_at    │ created_at      │ updated_at             │
│               │               │ updated_at      │                        │
├───────────────┼───────────────┴─────────────────┴────────────────────────┤
│               │ t_conversation_summary   │ t_message_feedback            │
│               │ 会话摘要表                │ 消息反馈表                     │
│               │                          │                               │
│               │ id                       │ id                            │
│               │ conversation_id (FK)     │ message_id (FK)               │
│               │ user_id                  │ user_id                       │
│               │ content (摘要文本)        │ rating (like/dislike)         │
│               │ last_message_id          │ comment                       │
│               │ created_at / updated_at  │ created_at / updated_at       │
└───────────────┴──────────────────────────┴───────────────────────────────┘
```

**t_department — 部门表**

- **id** — BigInteger 主键，Snowflake ID
- **name** — 部门名称，唯一且非空
- **description** — 部门描述，可为空
- **created_at / updated_at** — 由 TimestampMixin 自动维护

**t_user — 用户表**

- **id** — BigInteger 主键，Snowflake ID
- **username** — 用户名，唯一且非空
- **password_hash** — 密码哈希，非空
- **role** — 角色标识（如 `"user"`、`"admin"`），默认 `"user"`
- **avatar** — 头像 URL，可为空
- **department_id** — 所属部门，外键引用 `t_department.id`，可为空
- **created_at / updated_at** — 由 TimestampMixin 自动维护
- 索引：`ix_user_department_id`（department_id）

**t_conversation — 会话表**

- **id** — BigInteger 主键，Snowflake ID
- **user_id** — 所属用户，外键引用 `t_user.id`
- **title** — 会话标题，默认 `"新对话"`
- **last_message_time** — 最后消息时间，可为空
- **created_at / updated_at** — 由 TimestampMixin 自动维护
- 索引：`ix_conversation_user_id`（user_id）、`ix_conversation_user_id_id`（user_id, id 联合）

**t_message — 消息表**

- **id** — BigInteger 主键，Snowflake ID
- **conversation_id** — 所属会话，外键引用 `t_conversation.id`
- **user_id** — 发送用户，外键引用 `t_user.id`
- **role** — 消息角色，`"user"` 或 `"assistant"`
- **content** — 消息文本内容
- **thinking_content** — 模型思考过程内容（deep thinking），可为空
- **thinking_duration** — 思考耗时（秒），可为空
- **created_at / updated_at** — 由 TimestampMixin 自动维护
- 索引：`ix_message_conversation_id`（conversation_id）

**t_conversation_summary — 会话摘要表**

- **id** — BigInteger 主键，Snowflake ID
- **conversation_id** — 所属会话，外键引用 `t_conversation.id`
- **user_id** — 用户 ID
- **content** — 摘要文本
- **last_message_id** — 截止消息 ID，可为空
- **created_at / updated_at** — 由 TimestampMixin 自动维护
- 索引：`ix_conv_summary_conversation_id`（conversation_id）

**t_message_feedback — 消息反馈表**

- **id** — BigInteger 主键，Snowflake ID
- **message_id** — 反馈目标消息，外键引用 `t_message.id`
- **user_id** — 反馈用户 ID
- **rating** — 评分，`"like"` 或 `"dislike"`
- **comment** — 反馈评论，可为空
- **created_at / updated_at** — 由 TimestampMixin 自动维护

### 3.2 知识库域

```
┌───────────────────────────────────────────────────────────────────────────┐
│                         知识库域                                            │
├────────────────┬──────────────────┬──────────────┬────────────────────────┤
│ t_knowledge_   │ t_knowledge_     │ t_knowledge_ │ t_knowledge_           │
│  base          │  document        │  chunk       │  document_chunk_log    │
│ 知识库表        │ 文档表            │ 分块表        │ 分块日志表              │
│                │                  │              │                        │
│ id             │ id               │ id           │ id                     │
│ name           │ kb_id (FK)       │ kb_id (FK)   │ doc_id (FK)            │
│ description    │ doc_name         │ doc_id (FK)  │ status                 │
│ embedding_     │ file_url         │ chunk_index  │ extract_ms             │
│  model         │ file_type        │ content      │ chunk_ms               │
│ collection_    │ enabled          │ content_hash │ vectorize_ms           │
│  name          │ chunk_count      │ char_count   │ persist_ms             │
│ department_id  │ chunk_strategy   │ token_count  │ chunk_count            │
│   (FK→dept)    │ pipeline_id      │ keywords     │ error_message          │
│ created_at     │ process_mode     │ summary      │                        │
│ updated_at     │ created_at       │ enabled      │                        │
│                │ updated_at       │              │                        │
└────────────────┴──────────────────┴──────────────┴────────────────────────┘
```

**t_knowledge_base — 知识库表**

- **id** — BigInteger 主键，Snowflake ID
- **name** — 知识库名称，非空
- **description** — 知识库描述，可为空
- **embedding_model** — 使用的 Embedding 模型名称，非空
- **collection_name** — 对应的向量存储 Collection 名称，非空
- **department_id** — 所属部门，外键引用 `t_department.id`，可为空（用于部门级知识隔离）
- **created_at / updated_at** — 由 TimestampMixin 自动维护
- 索引：`ix_kb_department_id`（department_id）

**t_knowledge_document — 知识文档表**

- **id** — BigInteger 主键，Snowflake ID
- **kb_id** — 所属知识库，外键引用 `t_knowledge_base.id`
- **doc_name** — 文档名称，非空
- **file_url** — 文件存储路径，非空
- **file_type** — 文件类型（如 pdf、docx），非空
- **enabled** — 是否启用，默认 `True`
- **chunk_count** — 分块数量，默认 `0`
- **chunk_strategy** — 分块策略（如 `"fixed"`、`"semantic"`），默认 `"fixed"`
- **pipeline_id** — 关联的入库流水线 ID，可为空
- **process_mode** — 处理模式（如 `"auto"`、`"manual"`），默认 `"auto"`
- **created_at / updated_at** — 由 TimestampMixin 自动维护
- 索引：`ix_kd_kb_id`（kb_id）、`ix_kd_kb_id_doc_name`（kb_id, doc_name 联合）

**t_knowledge_chunk — 知识分块表**

- **id** — BigInteger 主键，Snowflake ID
- **kb_id** — 所属知识库，外键引用 `t_knowledge_base.id`
- **doc_id** — 所属文档，外键引用 `t_knowledge_document.id`
- **chunk_index** — 分块序号，非空
- **content** — 分块文本内容，非空
- **content_hash** — 内容哈希值，用于变更检测，非空
- **char_count** — 字符数，非空
- **token_count** — Token 数，可为空
- **keywords** — 关键词，可为空
- **summary** — 分块摘要，可为空
- **enabled** — 是否启用，默认 `True`
- 索引：`ix_kc_doc_id`（doc_id）、`ix_kc_kb_id`（kb_id）

**t_knowledge_document_chunk_log — 文档分块日志表**

- **id** — BigInteger 主键，Snowflake ID
- **doc_id** — 所属文档，外键引用 `t_knowledge_document.id`
- **status** — 处理状态，非空
- **extract_ms** — 文本提取耗时（毫秒），可为空
- **chunk_ms** — 分块处理耗时（毫秒），可为空
- **vectorize_ms** — 向量化耗时（毫秒），可为空
- **persist_ms** — 持久化耗时（毫秒），可为空
- **chunk_count** — 处理产生的分块数，可为空
- **error_message** — 错误信息，可为空
- 索引：`ix_dcl_doc_id`（doc_id）

### 3.3 RAG 意图与检索域

```
┌─────────────────────────────────────────────────────────────────┐
│                    RAG 意图与检索域                                │
├──────────────────────┬──────────────────────────────────────────┤
│ t_intent_node        │ t_query_term_mapping                     │
│ 意图树节点表          │ 关键词归一化映射表                         │
│                      │                                          │
│ id                   │ id                                       │
│ kb_id (FK)           │ domain                                   │
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
│ trace_id (PK, String)│ id                                       │
│ trace_name           │ trace_id (FK)                            │
│ conversation_id      │ node_id / parent_node_id                 │
│ task_id              │ depth                                    │
│ status               │ node_type / node_name                    │
│ duration_ms          │ module_name / function_name              │
│ error_message        │ status / duration_ms                     │
│                      │ extra_data (JSON)                        │
└──────────────────────┴──────────────────────────────────────────┘
```

**t_intent_node — 意图树节点表**

- **id** — BigInteger 主键，Snowflake ID
- **kb_id** — 所属知识库，外键引用 `t_knowledge_base.id`
- **intent_code** — 意图编码，非空
- **name** — 意图名称，非空
- **level** — 层级（0 = 根、1 = 中间、2 = 叶子），非空
- **parent_code** — 父节点编码，可为空
- **examples** — 示例文本，可为空
- **collection_name** — 关联的向量 Collection，可为空
- **kind** — 节点类型（0 = RAG、1 = TOOL），默认 0
- **system_prompt** — 系统提示词，可为空
- **rag_prompt** — RAG 提示词，可为空

**t_query_term_mapping — 关键词归一化映射表**

- **id** — BigInteger 主键，Snowflake ID
- **domain** — 领域标识，非空
- **source_term** — 原始关键词，非空
- **target_term** — 归一化目标词，非空
- **match_type** — 匹配方式（`"exact"` / `"fuzzy"` / `"prefix"`），默认 `"exact"`
- **priority** — 优先级，默认 0
- **enabled** — 是否启用，默认 `True`

**t_rag_trace_run — Trace 运行表**

- **trace_id** — String 主键，追踪 ID
- **trace_name** — 追踪名称，非空
- **conversation_id** — 关联会话 ID，可为空
- **task_id** — 关联任务 ID，可为空
- **status** — 执行状态，非空
- **duration_ms** — 总耗时（毫秒），可为空
- **error_message** — 错误信息，可为空

**t_rag_trace_node — Trace 节点记录表**

- **id** — BigInteger 主键，Snowflake ID
- **trace_id** — 所属追踪运行，外键引用 `t_rag_trace_run.trace_id`
- **node_id** — 节点 ID，非空
- **parent_node_id** — 父节点 ID，可为空
- **depth** — 节点深度，非空
- **node_type** — 节点类型，非空
- **node_name** — 节点名称，非空
- **module_name** — 模块名，可为空
- **function_name** — 函数名，可为空
- **status** — 执行状态，非空
- **duration_ms** — 耗时（毫秒），可为空
- **extra_data** — 附加数据，JSON 格式，可为空
- 索引：`ix_trace_node_trace_id`（trace_id）

### 3.4 入库流水线域

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       入库流水线域                                         │
├─────────────────┬──────────────────┬──────────────┬────────────────────┤
│ t_ingestion_    │ t_ingestion_     │ t_ingestion_ │ t_ingestion_       │
│  pipeline       │  pipeline_node   │  task        │  task_node         │
│ 流水线表         │ 流水线节点表      │ 任务表        │ 任务节点表          │
│                 │                  │              │                    │
│ id              │ id               │ id           │ id                 │
│ name            │ pipeline_id (FK) │ pipeline_id  │ task_id (FK)       │
│ description     │ node_id          │   (FK)       │ pipeline_id (FK)   │
│ created_at      │ node_type        │ source_type  │ node_id            │
│ updated_at      │ next_node_id     │ source_loc   │ node_type          │
│                 │ settings_json    │ status       │ status             │
│                 │ condition_json   │ chunk_count  │ duration_ms        │
│                 │                  │ metadata_json│ output_json        │
│                 │                  │ error_message│ error_message      │
│                 │                  │ created_at   │                    │
│                 │                  │ updated_at   │                    │
└─────────────────┴──────────────────┴──────────────┴────────────────────┘
```

**t_ingestion_pipeline — 入库流水线表**

- **id** — BigInteger 主键，Snowflake ID
- **name** — 流水线名称，非空
- **description** — 流水线描述，可为空
- **created_at / updated_at** — 由 TimestampMixin 自动维护

**t_ingestion_pipeline_node — 入库流水线节点表**

- **id** — BigInteger 主键，Snowflake ID
- **pipeline_id** — 所属流水线，外键引用 `t_ingestion_pipeline.id`
- **node_id** — 节点 ID，非空
- **node_type** — 节点类型，非空
- **next_node_id** — 下游节点 ID，可为空
- **settings_json** — 节点配置，JSON 格式，可为空
- **condition_json** — 条件配置，JSON 格式，可为空

**t_ingestion_task — 入库任务表**

- **id** — BigInteger 主键，Snowflake ID
- **pipeline_id** — 所属流水线，外键引用 `t_ingestion_pipeline.id`
- **source_type** — 来源类型，非空
- **source_loc** — 来源路径，非空
- **status** — 任务状态（如 `"PENDING"`），默认 `"PENDING"`
- **chunk_count** — 产生分块数，默认 0
- **metadata_json** — 元数据，JSON 格式，可为空
- **error_message** — 错误信息，可为空
- **created_at / updated_at** — 由 TimestampMixin 自动维护

**t_ingestion_task_node — 入库任务节点表**

- **id** — BigInteger 主键，Snowflake ID
- **task_id** — 所属任务，外键引用 `t_ingestion_task.id`
- **pipeline_id** — 所属流水线，外键引用 `t_ingestion_pipeline.id`
- **node_id** — 节点 ID，非空
- **node_type** — 节点类型，非空
- **status** — 执行状态，默认 `"PENDING"`
- **duration_ms** — 耗时（毫秒），可为空
- **output_json** — 输出数据，JSON 格式，可为空
- **error_message** — 错误信息，可为空

---

## 4. 表关联关系（ORM Relationship）

以下列出所有表之间的 SQLAlchemy ORM 双向关系：

**用户与会话域**

- **Department → User**：`Department.users` ⟷ `User.department`（一对多，部门拥有多个用户）
- **Department → KnowledgeBase**：`Department.knowledge_bases` ⟷ `KnowledgeBase.department`（一对多，部门拥有多个知识库）
- **User → Conversation**：`User.conversations` ⟷ `Conversation.user`（一对多，用户拥有多个会话）
- **User → Message**：`User.messages` ⟷ `Message.user`（一对多，用户拥有多条消息）
- **Conversation → Message**：`Conversation.messages` ⟷ `Message.conversation`（一对多，会话包含多条消息）
- **Conversation → ConversationSummary**：`Conversation.summaries` ⟷ `ConversationSummary.conversation`（一对多，会话拥有多条摘要）
- **Message → MessageFeedback**：`Message.feedbacks` ⟷ `MessageFeedback.message`（一对多，消息拥有多条反馈）

**知识库域**

- **KnowledgeBase → KnowledgeDocument**：`KnowledgeBase.documents` ⟷ `KnowledgeDocument.knowledge_base`（一对多，知识库包含多个文档）
- **KnowledgeBase → KnowledgeChunk**：`KnowledgeBase.chunks` ⟷ `KnowledgeChunk.knowledge_base`（一对多，知识库包含多个分块）
- **KnowledgeBase → IntentNode**：`KnowledgeBase.intent_nodes` ⟷ `IntentNode.knowledge_base`（一对多，知识库包含多个意图节点）
- **KnowledgeDocument → KnowledgeChunk**：`KnowledgeDocument.chunks` ⟷ `KnowledgeChunk.document`（一对多，文档包含多个分块）
- **KnowledgeDocument → DocumentChunkLog**：`KnowledgeDocument.chunk_logs` ⟷ `DocumentChunkLog.document`（一对多，文档包含多条分块日志）

**Trace 域**

- **RagTraceRun → RagTraceNode**：`RagTraceRun.nodes` ⟷ `RagTraceNode.trace_run`（一对多，一次追踪包含多个节点）

**入库流水线域**

- **IngestionPipeline → IngestionPipelineNode**：`IngestionPipeline.pipeline_nodes` ⟷ `IngestionPipelineNode.pipeline`（一对多，流水线包含多个节点）
- **IngestionPipeline → IngestionTask**：`IngestionPipeline.tasks` ⟷ `IngestionTask.pipeline`（一对多，流水线包含多个任务）
- **IngestionTask → IngestionTaskNode**：`IngestionTask.task_nodes` ⟷ `IngestionTaskNode.task`（一对多，任务包含多个执行节点）
- **IngestionTaskNode → IngestionPipeline**：`IngestionTaskNode.pipeline`（多对一，任务节点关联流水线，单向）

---

## 5. 向量存储

### 5.1 双引擎支持

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

## 6. ID 生成策略

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

> **注意**：`RagTraceRun` 是唯一例外，其主键 `trace_id` 使用 String 类型而非 Snowflake ID。

---

## 7. 数据库迁移

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

## 8. Redis 使用场景汇总

**限流排队**

- 数据结构：ZSET
- Key 格式：`ragent:queue:{user_id}`
- 说明：按时间戳排序的请求队列

**分布式锁**

- 数据结构：STRING
- Key 格式：`ragent:lock:{resource}`
- 说明：NX + PX 原子锁

**幂等去重**

- 数据结构：STRING
- Key 格式：`ragent:idempotent:{msg_id}`
- 说明：SETNX + TTL

**Snowflake ID**

- 数据结构：STRING
- Key 格式：`ragent:snowflake:worker_id`
- 说明：Lua 原子自增

**会话缓存**

- 数据结构：HASH
- Key 格式：`ragent:session:{session_id}`
- 说明：对话上下文缓存

**模型熔断状态**

- 数据结构：HASH
- Key 格式：`ragent:breaker:{model_id}`
- 说明：熔断器状态存储

**排队通知**

- 数据结构：PUB/SUB
- Key 格式：`ragent:notify:{user_id}`
- 说明：释放许可时广播
