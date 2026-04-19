"""
ragent.common.models —— SQLAlchemy ORM 数据模型

定义所有数据库表的 ORM 映射，共 17 张表，覆盖以下业务域：
  - 用户与会话域（User, Conversation, Message, ConversationSummary, MessageFeedback）
  - 知识库域（KnowledgeBase, KnowledgeDocument, KnowledgeChunk, DocumentChunkLog）
  - RAG 意图与检索域（IntentNode, QueryTermMapping）
  - Trace 域（RagTraceRun, RagTraceNode）
  - 入库流水线域（IngestionPipeline, IngestionPipelineNode, IngestionTask, IngestionTaskNode）

所有主键使用 BigInteger，以兼容 Snowflake 分布式 ID。
所有文档字符串和注释均使用中文。
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ---------------------------------------------------------------------------
# 基类与混入
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """ORM 声明性基类，所有模型的公共父类。"""
    pass


class TimestampMixin:
    """时间戳混入类，为模型提供 created_at / updated_at 自动维护字段。"""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


# ===========================================================================
# 用户与会话域
# ===========================================================================


class User(TimestampMixin, Base):
    """用户表 —— 存储平台注册用户的基本信息。"""

    __tablename__ = "t_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="user")
    avatar: Mapped[str | None] = mapped_column(String, nullable=True)

    # ---- 关系 ----
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user",
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="user",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role!r}>"


class Conversation(TimestampMixin, Base):
    """会话表 —— 记录用户与助手的对话会话。"""

    __tablename__ = "t_conversation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_user.id"), nullable=False,
    )
    title: Mapped[str] = mapped_column(String, nullable=False, default="新对话")
    last_message_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # ---- 关系 ----
    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
    )
    summaries: Mapped[list[ConversationSummary]] = relationship(
        back_populates="conversation",
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} title={self.title!r}>"


class Message(TimestampMixin, Base):
    """消息表 —— 存储会话中的每条消息（用户 / 助手）。"""

    __tablename__ = "t_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_conversation.id"), nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_user.id"), nullable=False,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" / "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    thinking_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    thinking_duration: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- 关系 ----
    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    user: Mapped[User] = relationship(back_populates="messages")
    feedbacks: Mapped[list[MessageFeedback]] = relationship(
        back_populates="message",
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} role={self.role!r}>"


class ConversationSummary(TimestampMixin, Base):
    """会话摘要表 —— 存储会话的历史摘要，用于长对话上下文压缩。"""

    __tablename__ = "t_conversation_summary"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_conversation.id"), nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # ---- 关系 ----
    conversation: Mapped[Conversation] = relationship(back_populates="summaries")

    def __repr__(self) -> str:
        return f"<ConversationSummary id={self.id} conversation_id={self.conversation_id}>"


class MessageFeedback(TimestampMixin, Base):
    """消息反馈表 —— 记录用户对助手回复的点赞/点踩反馈。"""

    __tablename__ = "t_message_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_message.id"), nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rating: Mapped[str] = mapped_column(String, nullable=False)  # "like" / "dislike"
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    message: Mapped[Message] = relationship(back_populates="feedbacks")

    def __repr__(self) -> str:
        return f"<MessageFeedback id={self.id} rating={self.rating!r}>"


# ===========================================================================
# 知识库域
# ===========================================================================


class KnowledgeBase(TimestampMixin, Base):
    """知识库表 —— 管理知识库的基本信息及关联的 Embedding 模型。"""

    __tablename__ = "t_knowledge_base"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    collection_name: Mapped[str] = mapped_column(String, nullable=False)

    # ---- 关系 ----
    documents: Mapped[list[KnowledgeDocument]] = relationship(
        back_populates="knowledge_base",
    )
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="knowledge_base",
    )
    intent_nodes: Mapped[list[IntentNode]] = relationship(
        back_populates="knowledge_base",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} name={self.name!r}>"


class KnowledgeDocument(TimestampMixin, Base):
    """知识文档表 —— 记录上传到知识库的文档元数据。"""

    __tablename__ = "t_knowledge_document"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_knowledge_base.id"), nullable=False,
    )
    doc_name: Mapped[str] = mapped_column(String, nullable=False)
    file_url: Mapped[str] = mapped_column(String, nullable=False)
    file_type: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_strategy: Mapped[str] = mapped_column(String, nullable=False, default="fixed")
    pipeline_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    process_mode: Mapped[str] = mapped_column(String, nullable=False, default="auto")

    # ---- 关系 ----
    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="document",
    )
    chunk_logs: Mapped[list[DocumentChunkLog]] = relationship(
        back_populates="document",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocument id={self.id} doc_name={self.doc_name!r}>"


class KnowledgeChunk(Base):
    """知识分块表 —— 存储文档切分后的文本分块及其统计信息。"""

    __tablename__ = "t_knowledge_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_knowledge_base.id"), nullable=False,
    )
    doc_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_knowledge_document.id"), nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keywords: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ---- 关系 ----
    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="chunks")
    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<KnowledgeChunk id={self.id} chunk_index={self.chunk_index}>"


class DocumentChunkLog(Base):
    """文档分块日志表 —— 记录文档处理各阶段的耗时与状态。"""

    __tablename__ = "t_knowledge_document_chunk_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    doc_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_knowledge_document.id"), nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    extract_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vectorize_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    persist_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunk_logs")

    def __repr__(self) -> str:
        return f"<DocumentChunkLog id={self.id} status={self.status!r}>"


# ===========================================================================
# RAG 意图与检索域
# ===========================================================================


class IntentNode(Base):
    """意图树节点表 —— 存储知识库的意图分类树结构。"""

    __tablename__ = "t_intent_node"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_knowledge_base.id"), nullable=False,
    )
    intent_code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 / 1 / 2
    parent_code: Mapped[str | None] = mapped_column(String, nullable=True)
    examples: Mapped[str | None] = mapped_column(Text, nullable=True)
    collection_name: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0=RAG, 1=TOOL
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    rag_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="intent_nodes")

    def __repr__(self) -> str:
        return f"<IntentNode id={self.id} intent_code={self.intent_code!r} name={self.name!r}>"


class QueryTermMapping(Base):
    """关键词归一化映射表 —— 用于查询改写时的术语标准化。"""

    __tablename__ = "t_query_term_mapping"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    source_term: Mapped[str] = mapped_column(String, nullable=False)
    target_term: Mapped[str] = mapped_column(String, nullable=False)
    match_type: Mapped[str] = mapped_column(
        String, nullable=False, default="exact",
    )  # exact / fuzzy / prefix
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return (
            f"<QueryTermMapping id={self.id} "
            f"source={self.source_term!r} target={self.target_term!r}>"
        )


# ===========================================================================
# Trace 域
# ===========================================================================


class RagTraceRun(Base):
    """Trace 运行表 —— 记录一次 RAG 请求的全链路追踪根节点。"""

    __tablename__ = "t_rag_trace_run"

    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    trace_name: Mapped[str] = mapped_column(String, nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    nodes: Mapped[list[RagTraceNode]] = relationship(
        back_populates="trace_run",
    )

    def __repr__(self) -> str:
        return f"<RagTraceRun trace_id={self.trace_id!r} status={self.status!r}>"


class RagTraceNode(Base):
    """Trace 节点记录表 —— 记录 RAG 链路中每个步骤的执行详情。"""

    __tablename__ = "t_rag_trace_node"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    trace_id: Mapped[str] = mapped_column(
        String, ForeignKey("t_rag_trace_run.trace_id"), nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    parent_node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    node_type: Mapped[str] = mapped_column(String, nullable=False)
    node_name: Mapped[str] = mapped_column(String, nullable=False)
    module_name: Mapped[str | None] = mapped_column(String, nullable=True)
    function_name: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ---- 关系 ----
    trace_run: Mapped[RagTraceRun] = relationship(back_populates="nodes")

    def __repr__(self) -> str:
        return f"<RagTraceNode id={self.id} node_id={self.node_id!r}>"


# ===========================================================================
# 入库流水线域
# ===========================================================================


class IngestionPipeline(TimestampMixin, Base):
    """入库流水线表 —— 定义文档处理流水线的元信息。"""

    __tablename__ = "t_ingestion_pipeline"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    pipeline_nodes: Mapped[list[IngestionPipelineNode]] = relationship(
        back_populates="pipeline",
    )
    tasks: Mapped[list[IngestionTask]] = relationship(
        back_populates="pipeline",
    )

    def __repr__(self) -> str:
        return f"<IngestionPipeline id={self.id} name={self.name!r}>"


class IngestionPipelineNode(Base):
    """入库流水线节点表 —— 定义流水线中每个处理节点的配置。"""

    __tablename__ = "t_ingestion_pipeline_node"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    pipeline_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_ingestion_pipeline.id"), nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    node_type: Mapped[str] = mapped_column(String, nullable=False)
    next_node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    settings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    condition_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ---- 关系 ----
    pipeline: Mapped[IngestionPipeline] = relationship(back_populates="pipeline_nodes")

    def __repr__(self) -> str:
        return f"<IngestionPipelineNode id={self.id} node_id={self.node_id!r}>"


class IngestionTask(TimestampMixin, Base):
    """入库任务表 —— 记录每条文档入库任务的执行状态。"""

    __tablename__ = "t_ingestion_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    pipeline_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_ingestion_pipeline.id"), nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_loc: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    pipeline: Mapped[IngestionPipeline] = relationship(back_populates="tasks")
    task_nodes: Mapped[list[IngestionTaskNode]] = relationship(
        back_populates="task",
    )

    def __repr__(self) -> str:
        return f"<IngestionTask id={self.id} status={self.status!r}>"


class IngestionTaskNode(Base):
    """入库任务节点表 —— 记录入库任务中每个流水线节点的执行详情。"""

    __tablename__ = "t_ingestion_task_node"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_ingestion_task.id"), nullable=False,
    )
    pipeline_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_ingestion_pipeline.id"), nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    node_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    task: Mapped[IngestionTask] = relationship(back_populates="task_nodes")
    pipeline: Mapped[IngestionPipeline] = relationship()

    def __repr__(self) -> str:
        return f"<IngestionTaskNode id={self.id} node_id={self.node_id!r} status={self.status!r}>"
