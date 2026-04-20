# Ragent Python 版 — RAG 问答核心链路

## 1. 模块定位

`ragent.rag` 模块是 Ragent 系统的核心业务引擎，负责编排完整的 RAG（Retrieval-Augmented Generation）问答管线。从用户发起提问到最终收到流式回答，整个链路由 `RAGChain` 类统一调度，依次经过认证校验、查询重写、意图分类、多路检索、Prompt 组装、LLM 流式生成等阶段，最终通过 SSE 协议将结果实时推送到前端。

**核心源文件：**

- **`ragent/rag/chain.py`** — RAGChain，主链路编排器，串联所有子步骤
- **`ragent/rag/rewriter/query_rewriter.py`** — QueryRewriter，查询重写（上下文补全 + 归一化 + 拆分）
- **`ragent/rag/intent/intent_classifier.py`** — IntentClassifier，LLM 驱动的意图分类
- **`ragent/rag/retrieval/retriever.py`** — RetrievalEngine，多路并行检索 + 去重/重排后处理
- **`ragent/rag/prompt/prompt_builder.py`** — PromptBuilder，消息列表组装
- **`ragent/rag/memory/session_memory.py`** — SessionMemoryManager，会话记忆持久化与自动摘要
- **`ragent/app/router.py`** — FastAPI 路由，`/chat` 端点、会话 CRUD 及知识库管理
- **`ragent/app/auth_router.py`** — 用户认证路由，注册/登录/me
- **`ragent/app/deps.py`** — CurrentUser / DbSession 依赖注入
- **`ragent/common/sse.py`** — SSE 流式事件封装
- **`ragent/common/models.py`** — ORM 模型（User、Conversation、Message、KnowledgeBase 等）

---

## 2. 认证集成

系统采用 **JWT Bearer Token** 认证方案，所有业务端点（`/chat`、`/conversations`、`/knowledge-bases` 等）均需携带有效 Token。

### 2.1 认证流程

```
客户端请求 (Authorization: Bearer <token>)
  │
  ▼
HTTPBearer 自动提取 Token
  │
  ▼
get_current_user() 依赖注入
  ├─ Token 缺失 → 401 "未提供认证凭据"
  ├─ Token 过期 → 401 "Token 已过期，请重新登录"
  ├─ Token 无效 → 401 "Token 无效"
  ├─ sub 字段缺失 → 401 "Token 缺少用户标识"
  ├─ 用户不存在 → 401 "用户不存在"
  └─ 成功 → 返回 User ORM 对象
```

### 2.2 依赖注入机制

认证通过 FastAPI 依赖注入实现，核心定义在 `deps.py`：

- **`CurrentUser`** — `Annotated[User, Depends(get_current_user)]`，在路由函数参数中声明即可自动触发认证
- **`DbSession`** — `Annotated[AsyncSession, Depends(get_db)]`，注入异步数据库会话
- **`_bearer_scheme`** — `HTTPBearer(auto_error=False)`，从请求头提取 `Authorization: Bearer ***`

路由中的典型用法：

```python
@router.post("/chat")
async def chat(
    request: ChatRequest,
    db: DbSession,
    current_user: CurrentUser,  # 自动触发 JWT 认证
) -> StreamingResponse:
    ...
```

### 2.3 认证端点

认证相关接口定义在 `auth_router.py`，挂载在 `/api/v1/auth` 路径下：

**POST /api/v1/auth/register — 用户注册**

- 请求体：`username`（3~32字符）+ `password`（6~128字符）+ `department_id`（可选）
- 检查用户名是否已存在（冲突返回 409）
- 生成 Snowflake ID + bcrypt 哈希密码
- 创建 User 记录（默认 `role="user"`）
- 自动生成 JWT 并返回

**POST /api/v1/auth/login — 用户登录**

- 请求体：`username` + `password`
- 按用户名查找用户，bcrypt 验证密码
- 验证失败返回 401
- 成功生成 JWT（`sub` 字段为用户 ID 字符串）并返回

**GET /api/v1/auth/me — 获取当前用户信息**

- 需 Bearer Token（`CurrentUser` 依赖注入）
- 返回当前用户的 `id`、`username`、`role`、`avatar`、`department_id`

**JWT Token 结构：**

- 载荷包含 `sub`（用户 ID 字符串）和标准 JWT 字段
- 通过 `ragent.infra.auth.decode_access_token()` 解码验证
- 使用 HS256 算法签名

### 2.4 chat 端点的认证集成

`POST /api/v1/chat` 端点通过 `CurrentUser` 依赖注入实现认证保护：

1. 请求到达时，`get_current_user()` 自动提取并验证 Bearer Token
2. 认证失败直接返回 401，不进入 RAG 链路
3. 认证成功后，`current_user` 对象包含完整的用户信息（`id`、`username`、`role`、`department_id`）
4. `current_user.id` 传入 `chain.ask(user_id=...)` 用于消息持久化关联
5. `current_user.department_id` 用于部门权限隔离（见下节）

---

## 3. 部门权限隔离

系统实现了基于部门的知识库访问控制，确保不同部门的数据相互隔离。权限检查通过 `_check_kb_dept_access(kb, user)` 函数统一实现。

### 3.1 权限规则

**角色判定逻辑：**

- **管理员（`role="admin"`）** — 可访问所有知识库，无部门限制
- **普通用户（`role="user"`）** — 仅可访问以下知识库：
  - 关联到**自己部门**的知识库（`kb.department_id == user.department_id`）
  - **未分配部门**的知识库（`kb.department_id is None`）
  - 跨部门访问返回 **403**（`"无权访问知识库 {kb.id}（部门隔离）"`）

### 3.2 检查函数实现

```python
def _check_kb_dept_access(kb: KnowledgeBase, user) -> str | None:
    """检查用户是否有权访问该知识库。

    admin 可访问所有；普通用户只能访问本部门或未分配部门的 KB。
    返回 None 表示有权限，否则返回错误消息。
    """
    if user.role == "admin":
        return None
    if kb.department_id is None:
        return None
    if kb.department_id != user.department_id:
        return f"无权访问知识库 {kb.id}（部门隔离）"
    return None
```

### 3.3 权限检查应用范围

部门权限检查覆盖所有知识库相关操作：

- **知识库列表**（`GET /knowledge-bases`）— 查询时自动过滤：admin 看全部，普通用户仅看到本部门 + 无部门关联的记录（SQL `WHERE` 层面过滤）
- **知识库详情**（`GET /knowledge-bases/{kb_id}`）— 查询后调用 `_check_kb_dept_access`，不通过返回 403
- **删除知识库**（`DELETE /knowledge-bases/{kb_id}`）— 同上，权限校验后才执行删除
- **文档上传**（`POST /documents/upload`）— 先检查目标知识库的部门权限
- **文档列表**（`GET /knowledge-bases/{kb_id}/documents`）— 先检查所属知识库的部门权限
- **删除文档**（`DELETE /documents/{doc_id}`）— 通过关联知识库间接检查部门权限

### 3.4 知识库创建时的部门关联

创建知识库时，自动将创建者的 `department_id` 写入知识库记录：

```python
kb = KnowledgeBase(
    ...
    department_id=current_user.department_id,
)
```

这意味着：
- 管理员创建的知识库如果不属于任何部门（`department_id=None`），所有用户可见
- 普通用户创建的知识库自动归属其所在部门，仅同部门用户和管理员可见

---

## 4. RAG 问答全链路

```
用户提问 (POST /api/v1/chat)
  │
  ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 0: JWT 认证                                              │
│   CurrentUser 依赖 → 解析 Bearer Token → 查询 User 记录       │
│   失败返回 401                                                │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 1: 接收问题 (ChatRequest)                                │
│   question (必填) + conversation_id (可选) +                   │
│   knowledge_base_id (可选) + user_id (可选)                    │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 2: 查询重写 (QueryRewriter)                               │
│   2a. 上下文补全 — LLM 根据对话历史解析代词/省略               │
│   2b. 关键词归一化 — 同义词映射为标准术语                      │
│   2c. 复杂问题拆分 — 多部分问题拆为子问题列表（可选）          │
│   失败时降级：使用原始问题                                     │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 3: 意图分类 (IntentClassifier)                            │
│   收集意图树叶节点 → LLM 为每个叶节点打分 → 阈值策略判定：     │
│   ≥ 0.8 高置信度直接命中 | 0.5~0.8 歧义检测 | < 0.5 全局搜索  │
│   失败时降级：返回 None（走全局搜索）                          │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 4: 向量检索 (RetrievalEngine)                             │
│   EmbeddingService 将查询文本 → 向量                           │
│   根据意图构建检索通道：                                       │
│     · 意图明确 → IntentDirectedChannel + GlobalVectorChannel  │
│     · 意图不明 → 仅 GlobalVectorChannel                       │
│   asyncio.gather 并行检索 → 去重 → 重排序                     │
│   失败时降级：返回空结果                                       │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 5: Context 组装 (PromptBuilder)                           │
│   System Prompt（角色定义）                                    │
│   + RAG Context（检索结果编号引用）                             │
│   + History（对话摘要 + 最近 3 轮消息）                        │
│   + Current Question（当前用户问题）                            │
│   意图节点可携带自定义 system_prompt / rag_prompt              │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 6: LLM 流式生成 (LLMService.stream_chat)                  │
│   异步生成器逐 token 输出                                      │
│   每个 token 包装为 sse_content 事件推送                       │
│   同时拼接完整回答文本（full_response）                        │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 7: SSE 流式返回                                           │
│   整个管线以 AsyncIterator[SSEEvent] 形式产出事件流            │
│   由 create_sse_response() 包装为 StreamingResponse            │
│   响应头: Content-Type: text/event-stream                      │
│           Cache-Control: no-cache                              │
│           X-Accel-Buffering: no                                 │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 8: 后处理 — 消息持久化                                    │
│   SessionMemoryManager.add_message() 写入 t_message 表：       │
│     · 保存 user 消息（用户原始问题）                            │
│     · 保存 assistant 消息（完整回答）                           │
│     · 更新 t_conversation.last_message_time                    │
│   若消息数 ≥ summarize_threshold，触发 LLM 自动摘要            │
│   摘要持久化到 t_conversation_summary 表                       │
└───────────────────────────────────────────────────────────────┘
```

---

## 5. RAGChain 实现

### 5.1 类结构

```python
class RAGChain:
    """RAG 问答主链路 —— 编排完整的 RAG 管线。"""

    def __init__(
        self,
        llm_service: LLMService,
        embedding_service: EmbeddingService,
        intent_tree: list[IntentNode] | None = None,   # 意图树，默认使用 Mock 数据
        *,
        window_size: int = 20,                          # 会话记忆窗口大小
    ) -> None: ...
```

初始化时创建以下子模块实例：

- **`self._llm`** (`LLMService`) — 大语言模型服务
- **`self._embedding`** (`EmbeddingService`) — 向量嵌入服务
- **`self._rewriter`** (`QueryRewriter`) — 查询重写器
- **`self._classifier`** (`IntentClassifier`) — 意图分类器
- **`self._retriever`** (`RetrievalEngine`) — 多路检索引擎
- **`self._memory`** (`SessionMemoryManager`) — 会话记忆管理器
- **`self._prompt_builder`** (`PromptBuilder`) — Prompt 组装器

### 5.2 ask() 方法 — 核心异步生成器

```python
async def ask(
    self,
    question: str,
    conversation_id: int | None = None,
    user_id: int | None = None,
    db_session: AsyncSession | None = None,
) -> AsyncIterator[SSEEvent]:
```

**使用方式：** 通过 `async for event in chain.ask(...)` 迭代消费 SSE 事件流。

**执行流程：**

1. **注入 DB 会话** — 若传入 `db_session`，注入到 SessionMemoryManager
2. **创建追踪段** — 手动创建 `TraceSpan(name="rag-pipeline")`，设置 `trace_id`
3. **发送 meta 事件** — `sse_meta({"status": "processing", "conversation_id": ...})`
4. **加载历史记忆** — 从数据库加载对话摘要 + 最近窗口消息
5. **查询重写** → 发送 `sse_meta({"stage": "query-rewrite"})`
6. **意图分类** → 发送 `sse_meta({"stage": "intent-classify"})`
7. **向量检索** → 发送 `sse_meta({"stage": "retrieval"})`
8. **Prompt 组装** → 发送 `sse_meta({"stage": "prompt-build"})`
9. **LLM 流式生成** → 发送 `sse_meta({"stage": "llm-generate"})`，逐 token 发送 `sse_content(token)`
10. **保存记忆** — 写入 user/assistant 两条消息到 `t_message`
11. **发送结束事件** — `sse_finish({"conversation_id", "intent", "confidence", "result_count"})`
12. **异常处理** — 捕获所有异常，发送 `sse_error(message, code="B2001")`

### 5.3 子步骤方法

每个子步骤方法内部创建独立的 `TraceSpan` 用于链路追踪，并挂载到父 span 的 children 列表中：

- **`_rewrite_step(question, history)`** — 调用 `QueryRewriter.rewrite()`，失败返回 `None`，使用原始问题
- **`_classify_step(query)`** — 调用 `IntentClassifier.classify()`，失败返回 `None`，走全局搜索
- **`_retrieval_step(query, intent)`** — 调用 `RetrievalEngine.search()`，失败返回空列表 `[]`
- **`_generate_step(messages)`** — 调用 `LLMService.stream_chat()`，失败发送 `sse_error(code="C3001")`
- **`_save_memory(conv_id, question, answer)`** — 调用 SessionMemoryManager 持久化，失败仅 warning，不影响回答

### 5.4 默认意图树（Mock）

`RAGChain` 内置一棵 Mock 意图树 `MOCK_INTENT_TREE`，包含以下节点：

```
DOMAIN_TECH (技术, level=1)
  ├── TOPIC_RAG  (RAG检索增强生成, level=2, collection=rag_knowledge)
  ├── TOPIC_LLM  (大语言模型, level=2, collection=llm_knowledge)
  └── TOPIC_EMB  (向量嵌入, level=2, collection=embedding_knowledge)
DOMAIN_BIZ (业务, level=1)
  └── TOPIC_PRODUCT (产品介绍, level=2, collection=product_knowledge)
```

每个叶节点包含 `examples`（示例问题）和 `collection_name`（关联的向量检索集合），用于意图分类时的 LLM 打分和定向检索。

---

## 6. 会话管理

会话管理通过 `router.py` 中的 4 个 REST 端点提供 CRUD 操作，所有端点均需 JWT 认证（`CurrentUser` 依赖注入）。

### 6.1 POST /api/v1/conversations — 创建会话

**请求体：**

```json
{
  "title": "新对话"
}
```

- `title`：可选，默认 `"新对话"`，最大 200 字符

**处理流程：**

1. 生成 Snowflake ID 作为会话 ID
2. 创建 `Conversation` 记录（关联 `current_user.id`）
3. 写入 `t_conversation` 表

**响应示例：**

```json
{
  "code": 0,
  "data": {
    "id": 7234901...,
    "title": "新对话",
    "user_id": 100001,
    "created_at": "2026-04-20T19:30:00"
  }
}
```

### 6.2 GET /api/v1/conversations — 会话列表（分页）

**查询参数：**

- **`page`** — 页码，≥ 1，默认 1
- **`page_size`** — 每页数量，1~100，默认 20

**处理逻辑：**

- 仅返回当前用户的会话（`WHERE user_id = current_user.id`）
- 按 `last_message_time DESC NULLS FIRST, created_at DESC` 排序
- 返回分页结构 `{items, total, page, page_size}`

### 6.3 GET /api/v1/conversations/{conv_id} — 会话详情 + 消息列表

**处理逻辑：**

1. 查询会话记录，同时校验 `user_id == current_user.id`（只能看自己的会话）
2. 查询 `t_message` 表，按 `created_at ASC` 排序获取该会话的所有消息
3. 返回会话信息 + 嵌套消息列表

**响应示例：**

```json
{
  "code": 0,
  "data": {
    "id": 7234901...,
    "title": "关于RAG的讨论",
    "user_id": 100001,
    "last_message_time": "2026-04-20T19:35:00",
    "created_at": "2026-04-20T19:30:00",
    "messages": [
      {"id": ..., "role": "user", "content": "什么是RAG？", "created_at": "..."},
      {"id": ..., "role": "assistant", "content": "RAG是检索增强生成...", "created_at": "..."}
    ]
  }
}
```

### 6.4 DELETE /api/v1/conversations/{conv_id} — 删除会话

**处理逻辑：**

1. 查询会话，校验用户归属
2. 调用 `db.delete(conv)` 删除会话记录
3. 依赖数据库级联删除或 ORM cascade 清理关联的 `t_message` 和 `t_conversation_summary` 记录

---

## 7. 消息持久化

### 7.1 数据模型

消息存储在 `t_message` 表中，对应的 ORM 模型为 `Message`：

```python
class Message(TimestampMixin, Base):
    __tablename__ = "t_message"

    id: Mapped[int]               # BigInteger 主键 (Snowflake ID)
    conversation_id: Mapped[int]  # 关联 t_conversation.id (FK)
    user_id: Mapped[int]          # 关联 t_user.id (FK)
    role: Mapped[str]             # "user" / "assistant"
    content: Mapped[str]          # 消息正文 (Text)
    thinking_content: Mapped[str | None]   # 思考过程内容（预留）
    thinking_duration: Mapped[float | None] # 思考耗时（预留）
    created_at: Mapped[datetime]  # 创建时间
    updated_at: Mapped[datetime]  # 更新时间
```

**索引：** `ix_message_conversation_id` — 加速按会话查询消息。

**关联关系：**

- `Message.conversation` → `Conversation.messages`
- `Message.user` → `User.messages`
- `Message.feedbacks` → `list[MessageFeedback]`

### 7.2 写入流程

消息写入由 `SessionMemoryManager.add_message()` 完成，在 RAG 链路的 `_save_memory()` 步骤中调用：

```python
async def _save_memory(self, conversation_id, question, answer, user_id=None):
    # 1. 保存用户消息
    await self._memory.add_message(conversation_id, "user", question, user_id=user_id)
    # 2. 保存助手回答
    await self._memory.add_message(conversation_id, "assistant", answer, user_id=user_id)
    # 3. 检查是否需要摘要
    if await self._memory.should_summarize(conversation_id):
        await self._memory.summarize(conversation_id)
```

**`add_message()` 内部操作：**

1. 生成 Snowflake ID
2. 创建 `Message` ORM 对象，设置 `conversation_id`、`user_id`、`role`、`content`
3. `db.add(msg)` 加入会话
4. 执行 `UPDATE t_conversation SET last_message_time = NOW() WHERE id = conversation_id`
5. `db.flush()` 刷新到数据库

### 7.3 自动摘要机制

当会话消息数 ≥ `summarize_threshold`（默认 20 条）时，触发自动摘要：

1. 从 `t_message` 加载该会话所有消息
2. 从 `t_conversation_summary` 获取已有的最新摘要
3. 用 LLM 对对话内容生成摘要
4. 若已有旧摘要，再用 LLM 合并新旧摘要
5. 将合并后的摘要写入 `t_conversation_summary` 表

摘要记录包含 `conversation_id`、`content`（摘要文本）、`last_message_id`（标记摘要覆盖到的最后一条消息）。

---

## 8. SSE 流式协议

### 8.1 协议格式

SSE 事件遵循标准 Server-Sent Events 规范，每个事件格式：

```
event: {事件类型}
data: {JSON 载荷}

```

响应头设置：

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

### 8.2 事件类型

- **`meta`**（`SSEEventType.META`）— 工厂函数 `sse_meta(data)`，元数据事件，传递管线阶段状态
- **`thinking`**（`SSEEventType.THINKING`）— 工厂函数 `sse_thinking(content)`，思考过程事件（预留，展示 AI 推理过程）
- **`content`**（`SSEEventType.CONTENT`）— 工厂函数 `sse_content(content)`，内容片段事件，前端逐个拼接为完整回答
- **`error`**（`SSEEventType.ERROR`）— 工厂函数 `sse_error(message, code)`，错误事件，前端收到后应终止处理
- **`finish`**（`SSEEventType.FINISH`）— 工厂函数 `sse_finish(data)`，结束事件，标记流的终止

### 8.3 典型事件流时序

一次完整的 RAG 问答会产生如下事件序列：

```
event: meta
data: {"status": "processing", "conversation_id": 7234901...}

event: meta
data: {"stage": "query-rewrite"}

event: meta
data: {"stage": "intent-classify"}

event: meta
data: {"stage": "retrieval"}

event: meta
data: {"stage": "prompt-build"}

event: meta
data: {"stage": "llm-generate"}

event: content
data: {"content": "RAG"}

event: content
data: {"content": "（检索增强生成）"}

event: content
data: {"content": "是一种..."}

...（多个 content 事件）

event: finish
data: {"conversation_id": 7234901..., "intent": "RAG检索增强生成", "confidence": 0.92, "result_count": 3}
```

### 8.4 SSEEvent 数据结构

```python
@dataclass(frozen=True)
class SSEEvent:
    event: str              # 事件类型
    data: str               # JSON 格式载荷
    id: str | None = None   # 可选事件 ID（用于断线重连）
    retry: int | None = None # 可选重连间隔（ms）
```

事件流通过 `sse_generator()` 异步生成器将 `SSEEvent` 对象转换为 SSE 协议文本，再由 `create_sse_response()` 包装为 FastAPI 的 `StreamingResponse` 返回给客户端。

---

## 9. 扩展点

### 9.1 自定义检索通道

实现 `SearchChannel` 抽象基类并重写 `search()` 方法：

```python
class SearchChannel(ABC):
    @abstractmethod
    async def search(self, query_embedding: list[float], top_k: int = 10) -> list[SearchResult]: ...
```

然后在 `RetrievalEngine._build_channels()` 中注册新通道，新通道将自动参与 `asyncio.gather` 并行检索。

### 9.2 自定义后处理器

当前 RetrievalEngine 内置两个后处理器：

- **`DeduplicatePostProcessor`** — 基于 `content_hash` 跨通道去重
- **`RerankPostProcessor`** — 重排序（当前 Mock，可接入 Cohere Rerank 等）

可通过构造函数参数注入自定义实现：

```python
engine = RetrievalEngine(
    embedding_service,
    dedup_processor=MyDedupProcessor(),
    rerank_processor=MyRerankProcessor(),
)
```

### 9.3 自定义查询重写

继承 `QueryRewriter` 并重写 `rewrite()` 方法，可替换上下文补全、归一化或拆分逻辑。也可通过 `term_mapping` 参数注入自定义同义词映射表。

### 9.4 自定义意图树

意图树支持两种方式配置：

1. **构造时传入** — `RAGChain(llm_service, embedding_service, intent_tree=my_tree)`
2. **数据库配置** — 意图节点存储在 `t_intent_node` 表中，可运行时增删改

每个意图节点支持自定义 `system_prompt` 和 `rag_prompt`，实现不同意图走不同的 Prompt 策略。

### 9.5 自定义 Prompt 模板

`PromptBuilder` 构造函数接受自定义默认模板：

```python
builder = PromptBuilder(
    default_system_prompt="你的自定义系统提示词...",
    default_rag_prompt="基于以下资料回答：{context}...",
)
```

也可在意图节点级别通过 `system_prompt` / `rag_prompt` 字段指定，会覆盖默认模板。

### 9.6 扩展汇总

- **新增检索通道** — 实现 `SearchChannel` 抽象类的 `search()` 方法
- **替换去重/重排** — 注入自定义 `DeduplicatePostProcessor` / `RerankPostProcessor`
- **自定义查询重写** — 继承 `QueryRewriter` 并重写 `rewrite()` 方法
- **自定义意图树** — 构造时传入或数据库配置 `IntentNode` 数据类
- **自定义 Prompt** — 按意图节点配置或全局替换 `PromptBuilder` 构造参数
- **新增 SSE 事件类型** — 扩展 `SSEEventType` 常量并更新 `sse_generator()`
