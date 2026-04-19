"""
ragent 数据模型单元测试

覆盖内容：
  - 所有 17 张表的模型实例化
  - 表名与规格一致性
  - 列类型与列默认值（通过列元数据验证）
  - 关系属性存在性验证
  - 外键定义验证
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import BigInteger, Boolean, JSON, String, Text

from ragent.common.models import (
    Base,
    Conversation,
    ConversationSummary,
    DocumentChunkLog,
    IngestionPipeline,
    IngestionPipelineNode,
    IngestionTask,
    IngestionTaskNode,
    IntentNode,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    Message,
    MessageFeedback,
    QueryTermMapping,
    RagTraceNode,
    RagTraceRun,
    TimestampMixin,
    User,
)

# ==========================================================================
# 辅助：定义期望的表名集合
# ==========================================================================

EXPECTED_TABLE_NAMES = {
    "t_user",
    "t_conversation",
    "t_message",
    "t_conversation_summary",
    "t_message_feedback",
    "t_knowledge_base",
    "t_knowledge_document",
    "t_knowledge_chunk",
    "t_knowledge_document_chunk_log",
    "t_intent_node",
    "t_query_term_mapping",
    "t_rag_trace_run",
    "t_rag_trace_node",
    "t_ingestion_pipeline",
    "t_ingestion_pipeline_node",
    "t_ingestion_task",
    "t_ingestion_task_node",
}

MODEL_CLASSES = [
    User,
    Conversation,
    Message,
    ConversationSummary,
    MessageFeedback,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeChunk,
    DocumentChunkLog,
    IntentNode,
    QueryTermMapping,
    RagTraceRun,
    RagTraceNode,
    IngestionPipeline,
    IngestionPipelineNode,
    IngestionTask,
    IngestionTaskNode,
]


# ==========================================================================
# 测试：模型总数与表名
# ==========================================================================


class TestTableNames:
    """验证所有模型表名与架构设计一致。"""

    def test_model_count(self) -> None:
        """共 17 张表。"""
        assert len(MODEL_CLASSES) == 17

    def test_all_tables_present(self) -> None:
        """所有表名均在期望集合中。"""
        actual = {cls.__tablename__ for cls in MODEL_CLASSES}
        assert actual == EXPECTED_TABLE_NAMES

    @pytest.mark.parametrize("model_cls", MODEL_CLASSES)
    def test_single_tablename(self, model_cls: type) -> None:
        """每个模型都有 __tablename__ 属性且在期望集合中。"""
        assert hasattr(model_cls, "__tablename__")
        assert model_cls.__tablename__ in EXPECTED_TABLE_NAMES


# ==========================================================================
# 测试：模型实例化
# ==========================================================================


class TestInstantiation:
    """验证所有模型可以实例化并赋予属性。"""

    def test_user(self) -> None:
        u = User(id=1, username="alice", password_hash="h", role="user")
        assert u.username == "alice"
        assert u.role == "user"
        assert u.avatar is None

    def test_conversation(self) -> None:
        c = Conversation(id=10, user_id=1, title="测试对话")
        assert c.title == "测试对话"

    def test_message(self) -> None:
        m = Message(
            id=100,
            conversation_id=10,
            user_id=1,
            role="user",
            content="你好",
        )
        assert m.content == "你好"
        assert m.thinking_content is None
        assert m.thinking_duration is None

    def test_conversation_summary(self) -> None:
        s = ConversationSummary(
            id=200, conversation_id=10, user_id=1, content="摘要"
        )
        assert s.content == "摘要"
        assert s.last_message_id is None

    def test_message_feedback(self) -> None:
        f = MessageFeedback(id=300, message_id=100, user_id=1, rating="like")
        assert f.rating == "like"
        assert f.comment is None

    def test_knowledge_base(self) -> None:
        kb = KnowledgeBase(
            id=1000,
            name="测试库",
            embedding_model="text-embedding-3-small",
            collection_name="col_test",
        )
        assert kb.name == "测试库"
        assert kb.description is None

    def test_knowledge_document(self) -> None:
        doc = KnowledgeDocument(
            id=2000,
            kb_id=1000,
            doc_name="test.pdf",
            file_url="/tmp/test.pdf",
            file_type="pdf",
            enabled=True,
            chunk_count=0,
            chunk_strategy="fixed",
            process_mode="auto",
        )
        assert doc.enabled is True
        assert doc.chunk_count == 0
        assert doc.chunk_strategy == "fixed"
        assert doc.process_mode == "auto"

    def test_knowledge_chunk(self) -> None:
        chunk = KnowledgeChunk(
            id=3000,
            kb_id=1000,
            doc_id=2000,
            chunk_index=0,
            content="分块内容",
            content_hash="abc123",
            char_count=4,
            enabled=True,
        )
        assert chunk.enabled is True
        assert chunk.token_count is None

    def test_document_chunk_log(self) -> None:
        log = DocumentChunkLog(id=4000, doc_id=2000, status="SUCCESS", chunk_count=5)
        assert log.extract_ms is None
        assert log.chunk_count == 5

    def test_intent_node(self) -> None:
        node = IntentNode(
            id=5000,
            kb_id=1000,
            intent_code="FINANCE_TAX",
            name="税务咨询",
            level=0,
            kind=0,
        )
        assert node.kind == 0
        assert node.parent_code is None

    def test_query_term_mapping(self) -> None:
        qm = QueryTermMapping(
            id=6000,
            domain="finance",
            source_term="个税",
            target_term="个人所得税",
            match_type="exact",
            priority=0,
            enabled=True,
        )
        assert qm.match_type == "exact"
        assert qm.priority == 0
        assert qm.enabled is True

    def test_rag_trace_run(self) -> None:
        run = RagTraceRun(
            trace_id="trace-001",
            trace_name="RAG查询",
            status="OK",
            duration_ms=120,
        )
        assert run.trace_id == "trace-001"
        assert run.conversation_id is None

    def test_rag_trace_node(self) -> None:
        node = RagTraceNode(
            id=7000,
            trace_id="trace-001",
            node_id="retrieve",
            parent_node_id=None,
            depth=1,
            node_type="retrieval",
            node_name="检索",
            status="OK",
        )
        assert node.extra_data is None

    def test_ingestion_pipeline(self) -> None:
        p = IngestionPipeline(id=8000, name="默认流水线")
        assert p.description is None

    def test_ingestion_pipeline_node(self) -> None:
        pn = IngestionPipelineNode(
            id=8100,
            pipeline_id=8000,
            node_id="parser",
            node_type="PDFParser",
        )
        assert pn.next_node_id is None
        assert pn.settings_json is None

    def test_ingestion_task(self) -> None:
        t = IngestionTask(
            id=9000,
            pipeline_id=8000,
            source_type="file",
            source_loc="/tmp/test.pdf",
            status="PENDING",
            chunk_count=0,
        )
        assert t.status == "PENDING"
        assert t.chunk_count == 0

    def test_ingestion_task_node(self) -> None:
        tn = IngestionTaskNode(
            id=9100,
            task_id=9000,
            pipeline_id=8000,
            node_id="parser",
            node_type="PDFParser",
            status="PENDING",
        )
        assert tn.status == "PENDING"
        assert tn.duration_ms is None


# ==========================================================================
# 测试：列默认值（通过列元数据验证）
# ==========================================================================


def _get_column_default_value(model_cls: type, col_name: str) -> object:
    """获取列的 Python 级默认值（scalar default）。"""
    col = model_cls.__table__.c[col_name]
    dflt = col.default
    if dflt is None:
        return None
    return dflt.arg


class TestColumnDefaults:
    """验证列定义中的默认值（SQL / 列元数据级别）。"""

    def test_user_role_default(self) -> None:
        assert _get_column_default_value(User, "role") == "user"

    def test_conversation_title_default(self) -> None:
        assert _get_column_default_value(Conversation, "title") == "新对话"

    def test_knowledge_document_enabled_default(self) -> None:
        assert _get_column_default_value(KnowledgeDocument, "enabled") is True

    def test_knowledge_document_chunk_count_default(self) -> None:
        assert _get_column_default_value(KnowledgeDocument, "chunk_count") == 0

    def test_knowledge_document_chunk_strategy_default(self) -> None:
        assert _get_column_default_value(KnowledgeDocument, "chunk_strategy") == "fixed"

    def test_knowledge_document_process_mode_default(self) -> None:
        assert _get_column_default_value(KnowledgeDocument, "process_mode") == "auto"

    def test_knowledge_chunk_enabled_default(self) -> None:
        assert _get_column_default_value(KnowledgeChunk, "enabled") is True

    def test_intent_node_kind_default(self) -> None:
        assert _get_column_default_value(IntentNode, "kind") == 0

    def test_query_term_mapping_match_type_default(self) -> None:
        assert _get_column_default_value(QueryTermMapping, "match_type") == "exact"

    def test_query_term_mapping_priority_default(self) -> None:
        assert _get_column_default_value(QueryTermMapping, "priority") == 0

    def test_query_term_mapping_enabled_default(self) -> None:
        assert _get_column_default_value(QueryTermMapping, "enabled") is True

    def test_ingestion_task_status_default(self) -> None:
        assert _get_column_default_value(IngestionTask, "status") == "PENDING"

    def test_ingestion_task_chunk_count_default(self) -> None:
        assert _get_column_default_value(IngestionTask, "chunk_count") == 0

    def test_ingestion_task_node_status_default(self) -> None:
        assert _get_column_default_value(IngestionTaskNode, "status") == "PENDING"


# ==========================================================================
# 测试：列类型映射
# ==========================================================================


class TestColumnTypes:
    """验证关键列使用正确的 SQLAlchemy 类型。"""

    def test_biginteger_pks(self) -> None:
        """大部分表的主键应为 BigInteger。"""
        big_int_pk_models = [
            User, Conversation, Message, ConversationSummary, MessageFeedback,
            KnowledgeBase, KnowledgeDocument, KnowledgeChunk, DocumentChunkLog,
            IntentNode, QueryTermMapping,
            RagTraceNode,
            IngestionPipeline, IngestionPipelineNode, IngestionTask, IngestionTaskNode,
        ]
        for cls in big_int_pk_models:
            col = cls.__table__.primary_key.columns.values()[0]
            assert isinstance(col.type, BigInteger), (
                f"{cls.__name__} 主键类型应为 BigInteger，实际为 {type(col.type).__name__}"
            )

    def test_trace_run_string_pk(self) -> None:
        """RagTraceRun 的主键应为 String。"""
        col = RagTraceRun.__table__.primary_key.columns.values()[0]
        assert isinstance(col.type, String)

    def test_text_columns(self) -> None:
        """验证 Text 类型列。"""
        msg_table = Message.__table__
        assert isinstance(msg_table.c.content.type, Text)
        assert isinstance(msg_table.c.thinking_content.type, Text)

    def test_boolean_columns(self) -> None:
        """验证 Boolean 类型列。"""
        assert isinstance(KnowledgeDocument.__table__.c.enabled.type, Boolean)
        assert isinstance(KnowledgeChunk.__table__.c.enabled.type, Boolean)
        assert isinstance(QueryTermMapping.__table__.c.enabled.type, Boolean)

    def test_json_columns(self) -> None:
        """验证 JSON 类型列。"""
        assert isinstance(RagTraceNode.__table__.c.extra_data.type, JSON)
        assert isinstance(IngestionPipelineNode.__table__.c.settings_json.type, JSON)
        assert isinstance(IngestionTask.__table__.c.metadata_json.type, JSON)
        assert isinstance(IngestionTaskNode.__table__.c.output_json.type, JSON)


# ==========================================================================
# 测试：关系属性
# ==========================================================================


class TestRelationships:
    """验证关系属性存在且方向正确（不需要真实数据库）。"""

    def test_user_has_conversations(self) -> None:
        u = User(id=1, username="a", password_hash="h")
        assert hasattr(u, "conversations")

    def test_user_has_messages(self) -> None:
        u = User(id=1, username="a", password_hash="h")
        assert hasattr(u, "messages")

    def test_conversation_has_user(self) -> None:
        c = Conversation(id=1, user_id=1)
        assert hasattr(c, "user")

    def test_conversation_has_messages(self) -> None:
        c = Conversation(id=1, user_id=1)
        assert hasattr(c, "messages")

    def test_conversation_has_summaries(self) -> None:
        c = Conversation(id=1, user_id=1)
        assert hasattr(c, "summaries")

    def test_message_has_conversation(self) -> None:
        m = Message(id=1, conversation_id=1, user_id=1, role="user", content="hi")
        assert hasattr(m, "conversation")

    def test_message_has_user(self) -> None:
        m = Message(id=1, conversation_id=1, user_id=1, role="user", content="hi")
        assert hasattr(m, "user")

    def test_message_has_feedbacks(self) -> None:
        m = Message(id=1, conversation_id=1, user_id=1, role="user", content="hi")
        assert hasattr(m, "feedbacks")

    def test_message_feedback_has_message(self) -> None:
        f = MessageFeedback(id=1, message_id=1, user_id=1, rating="like")
        assert hasattr(f, "message")

    def test_conversation_summary_has_conversation(self) -> None:
        s = ConversationSummary(id=1, conversation_id=1, user_id=1, content="x")
        assert hasattr(s, "conversation")

    def test_knowledge_base_has_documents(self) -> None:
        kb = KnowledgeBase(id=1, name="kb", embedding_model="e", collection_name="c")
        assert hasattr(kb, "documents")

    def test_knowledge_base_has_chunks(self) -> None:
        kb = KnowledgeBase(id=1, name="kb", embedding_model="e", collection_name="c")
        assert hasattr(kb, "chunks")

    def test_knowledge_base_has_intent_nodes(self) -> None:
        kb = KnowledgeBase(id=1, name="kb", embedding_model="e", collection_name="c")
        assert hasattr(kb, "intent_nodes")

    def test_knowledge_document_has_kb(self) -> None:
        doc = KnowledgeDocument(id=1, kb_id=1, doc_name="a", file_url="b", file_type="pdf")
        assert hasattr(doc, "knowledge_base")

    def test_knowledge_document_has_chunks(self) -> None:
        doc = KnowledgeDocument(id=1, kb_id=1, doc_name="a", file_url="b", file_type="pdf")
        assert hasattr(doc, "chunks")

    def test_knowledge_document_has_chunk_logs(self) -> None:
        doc = KnowledgeDocument(id=1, kb_id=1, doc_name="a", file_url="b", file_type="pdf")
        assert hasattr(doc, "chunk_logs")

    def test_knowledge_chunk_has_kb(self) -> None:
        c = KnowledgeChunk(id=1, kb_id=1, doc_id=1, chunk_index=0,
                           content="x", content_hash="h", char_count=1)
        assert hasattr(c, "knowledge_base")

    def test_knowledge_chunk_has_document(self) -> None:
        c = KnowledgeChunk(id=1, kb_id=1, doc_id=1, chunk_index=0,
                           content="x", content_hash="h", char_count=1)
        assert hasattr(c, "document")

    def test_document_chunk_log_has_document(self) -> None:
        log = DocumentChunkLog(id=1, doc_id=1, status="OK")
        assert hasattr(log, "document")

    def test_intent_node_has_knowledge_base(self) -> None:
        n = IntentNode(id=1, kb_id=1, intent_code="X", name="Y", level=0)
        assert hasattr(n, "knowledge_base")

    def test_rag_trace_run_has_nodes(self) -> None:
        run = RagTraceRun(trace_id="t1", trace_name="n", status="OK")
        assert hasattr(run, "nodes")

    def test_rag_trace_node_has_trace_run(self) -> None:
        node = RagTraceNode(
            id=1, trace_id="t1", node_id="n", depth=1,
            node_type="t", node_name="n", status="OK",
        )
        assert hasattr(node, "trace_run")

    def test_ingestion_pipeline_has_pipeline_nodes(self) -> None:
        p = IngestionPipeline(id=1, name="p")
        assert hasattr(p, "pipeline_nodes")

    def test_ingestion_pipeline_has_tasks(self) -> None:
        p = IngestionPipeline(id=1, name="p")
        assert hasattr(p, "tasks")

    def test_ingestion_pipeline_node_has_pipeline(self) -> None:
        pn = IngestionPipelineNode(id=1, pipeline_id=1, node_id="n", node_type="t")
        assert hasattr(pn, "pipeline")

    def test_ingestion_task_has_pipeline(self) -> None:
        t = IngestionTask(id=1, pipeline_id=1, source_type="file", source_loc="/tmp/x")
        assert hasattr(t, "pipeline")

    def test_ingestion_task_has_task_nodes(self) -> None:
        t = IngestionTask(id=1, pipeline_id=1, source_type="file", source_loc="/tmp/x")
        assert hasattr(t, "task_nodes")

    def test_ingestion_task_node_has_task(self) -> None:
        tn = IngestionTaskNode(id=1, task_id=1, pipeline_id=1, node_id="n", node_type="t")
        assert hasattr(tn, "task")

    def test_ingestion_task_node_has_pipeline(self) -> None:
        tn = IngestionTaskNode(id=1, task_id=1, pipeline_id=1, node_id="n", node_type="t")
        assert hasattr(tn, "pipeline")


# ==========================================================================
# 测试：__repr__
# ==========================================================================


class TestRepr:
    """验证所有模型都有 __repr__ 方法。"""

    @pytest.mark.parametrize("model_cls", MODEL_CLASSES)
    def test_repr_defined(self, model_cls: type) -> None:
        """每个模型都定义了 __repr__。"""
        assert "__repr__" in model_cls.__dict__


# ==========================================================================
# 测试：ForeignKey 存在
# ==========================================================================


class TestForeignKeys:
    """验证外键列正确定义。"""

    def test_conversation_user_fk(self) -> None:
        col = Conversation.__table__.c.user_id
        assert len(col.foreign_keys) == 1

    def test_message_conversation_fk(self) -> None:
        col = Message.__table__.c.conversation_id
        assert len(col.foreign_keys) == 1

    def test_message_user_fk(self) -> None:
        col = Message.__table__.c.user_id
        assert len(col.foreign_keys) == 1

    def test_message_feedback_message_fk(self) -> None:
        col = MessageFeedback.__table__.c.message_id
        assert len(col.foreign_keys) == 1

    def test_knowledge_document_kb_fk(self) -> None:
        col = KnowledgeDocument.__table__.c.kb_id
        assert len(col.foreign_keys) == 1

    def test_knowledge_chunk_kb_fk(self) -> None:
        col = KnowledgeChunk.__table__.c.kb_id
        assert len(col.foreign_keys) == 1

    def test_knowledge_chunk_doc_fk(self) -> None:
        col = KnowledgeChunk.__table__.c.doc_id
        assert len(col.foreign_keys) == 1

    def test_document_chunk_log_doc_fk(self) -> None:
        col = DocumentChunkLog.__table__.c.doc_id
        assert len(col.foreign_keys) == 1

    def test_intent_node_kb_fk(self) -> None:
        col = IntentNode.__table__.c.kb_id
        assert len(col.foreign_keys) == 1

    def test_rag_trace_node_trace_id_fk(self) -> None:
        col = RagTraceNode.__table__.c.trace_id
        assert len(col.foreign_keys) == 1

    def test_ingestion_pipeline_node_pipeline_fk(self) -> None:
        col = IngestionPipelineNode.__table__.c.pipeline_id
        assert len(col.foreign_keys) == 1

    def test_ingestion_task_pipeline_fk(self) -> None:
        col = IngestionTask.__table__.c.pipeline_id
        assert len(col.foreign_keys) == 1

    def test_ingestion_task_node_task_fk(self) -> None:
        col = IngestionTaskNode.__table__.c.task_id
        assert len(col.foreign_keys) == 1

    def test_ingestion_task_node_pipeline_fk(self) -> None:
        col = IngestionTaskNode.__table__.c.pipeline_id
        assert len(col.foreign_keys) == 1
