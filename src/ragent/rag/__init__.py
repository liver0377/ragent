"""RAG 核心模块 —— RAG 管线的完整实现。

子模块：
    - rewriter:       问题重写器
    - intent:         意图分类器
    - retrieval:      多路检索引擎
    - memory:         会话记忆管理
    - prompt:         Prompt 组装器
    - chain:          RAG 问答主链路
"""

from ragent.rag.chain import RAGChain
from ragent.rag.intent.intent_classifier import IntentClassifier, IntentNode, IntentResult
from ragent.rag.memory.session_memory import MemoryMessage, SessionMemory, SessionMemoryManager
from ragent.rag.prompt.prompt_builder import PromptBuilder
from ragent.rag.retrieval.retriever import (
    DeduplicatePostProcessor,
    GlobalVectorChannel,
    IntentDirectedChannel,
    RerankPostProcessor,
    RetrievalEngine,
    SearchChannel,
    SearchResult,
)
from ragent.rag.rewriter.query_rewriter import QueryRewriter, RewriteResult

__all__ = [
    # Chain
    "RAGChain",
    # Rewriter
    "QueryRewriter",
    "RewriteResult",
    # Intent
    "IntentClassifier",
    "IntentNode",
    "IntentResult",
    # Retrieval
    "RetrievalEngine",
    "SearchChannel",
    "SearchResult",
    "IntentDirectedChannel",
    "GlobalVectorChannel",
    "DeduplicatePostProcessor",
    "RerankPostProcessor",
    # Memory
    "SessionMemoryManager",
    "SessionMemory",
    "MemoryMessage",
    # Prompt
    "PromptBuilder",
]
