"""多路检索引擎 —— 向量检索、多通道并行搜索与后处理。

核心职责：
    1. ``SearchChannel`` 抽象基类 —— 定义检索通道接口
    2. ``IntentDirectedChannel`` —— 基于意图的定向检索通道
    3. ``GlobalVectorChannel`` —— 全局向量检索通道
    4. ``DeduplicatePostProcessor`` —— 基于 content_hash 的去重后处理器
    5. ``RerankPostProcessor`` —— 重排序后处理器（接口预留）
    6. ``RetrievalEngine`` —— 多路检索引擎，编排通道并行检索 + 后处理
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ragent.infra.ai.embedding_service import EmbeddingService
from ragent.rag.intent.intent_classifier import IntentNode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """检索结果条目。

    Attributes:
        chunk_id:       文档块唯一标识。
        content:        文档块文本内容。
        score:          相似度分数。
        metadata:       元数据字典。
        source_channel: 来源通道标识。
        content_hash:   内容哈希值，用于去重。
    """

    chunk_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    source_channel: str = ""
    content_hash: str = ""

    def __post_init__(self) -> None:
        """自动计算 content_hash。"""
        if not self.content_hash and self.content:
            self.content_hash = hashlib.md5(self.content.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 检索通道抽象基类
# ---------------------------------------------------------------------------


class SearchChannel(ABC):
    """检索通道抽象基类。

    所有具体的检索通道（如 Milvus、pgvector、ES 等）都应继承此类并实现 ``search`` 方法。
    """

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[SearchResult]:
        """执行向量检索。

        Args:
            query_embedding: 查询文本的向量表示。
            top_k:           返回的最大结果数。

        Returns:
            list[SearchResult]: 检索结果列表。
        """
        ...


# ---------------------------------------------------------------------------
# 意图定向检索通道
# ---------------------------------------------------------------------------


class IntentDirectedChannel(SearchChannel):
    """基于意图的定向检索通道 —— 检索意图关联的特定集合。

    当用户意图明确时，仅在意图对应的 collection 中检索，
    提高检索的精确度和效率。

    当前为 Mock 实现，实际部署时替换为 Milvus/pgvector 调用。
    """

    def __init__(self, intent: IntentNode) -> None:
        """初始化意图定向检索通道。

        Args:
            intent: 意图节点，包含 collection_name 信息。
        """
        self._intent = intent
        self._channel_name = f"intent-{intent.intent_code}"

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[SearchResult]:
        """在意图对应的集合中执行向量检索（Mock 实现）。

        Args:
            query_embedding: 查询向量。
            top_k:           返回的最大结果数。

        Returns:
            list[SearchResult]: Mock 检索结果。
        """
        collection = self._intent.collection_name or "default"
        logger.debug(
            "意图定向检索: intent=%s, collection=%s, top_k=%d",
            self._intent.name,
            collection,
            top_k,
        )

        # Mock：返回模拟结果
        # 实际实现中，此处应调用 Milvus/pgvector 的 search 接口
        mock_results: list[SearchResult] = [
            SearchResult(
                chunk_id=f"mock-{self._intent.intent_code}-{uuid.uuid4().hex[:8]}",
                content=f"[{self._intent.name}] 关于该主题的参考内容片段 #{i + 1}。",
                score=0.95 - i * 0.05,
                metadata={
                    "collection": collection,
                    "intent_code": self._intent.intent_code,
                    "source": "intent_directed",
                },
                source_channel=self._channel_name,
            )
            for i in range(min(top_k, 3))
        ]

        return mock_results


# ---------------------------------------------------------------------------
# 全局向量检索通道
# ---------------------------------------------------------------------------


class GlobalVectorChannel(SearchChannel):
    """全局向量检索通道 —— 在所有集合中进行检索。

    当用户意图不明确时使用，覆盖所有可用的知识库集合。

    当前为 Mock 实现，实际部署时替换为 Milvus/pgvector 调用。
    """

    def __init__(self, collections: list[str] | None = None) -> None:
        """初始化全局向量检索通道。

        Args:
            collections: 要检索的集合名称列表。若为 ``None`` 则检索所有集合。
        """
        self._collections = collections or ["global_default"]
        self._channel_name = "global-vector"

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[SearchResult]:
        """在所有集合中执行向量检索（Mock 实现）。

        Args:
            query_embedding: 查询向量。
            top_k:           返回的最大结果数。

        Returns:
            list[SearchResult]: Mock 检索结果。
        """
        logger.debug(
            "全局向量检索: collections=%s, top_k=%d",
            self._collections,
            top_k,
        )

        # Mock：返回模拟结果
        mock_results: list[SearchResult] = [
            SearchResult(
                chunk_id=f"mock-global-{uuid.uuid4().hex[:8]}",
                content=f"[全局搜索] 通用参考内容片段 #{i + 1}。",
                score=0.85 - i * 0.05,
                metadata={
                    "collections": self._collections,
                    "source": "global_vector",
                },
                source_channel=self._channel_name,
            )
            for i in range(min(top_k, 5))
        ]

        return mock_results


# ---------------------------------------------------------------------------
# 辅助函数：余弦相似度
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。

    Args:
        a: 向量 A。
        b: 向量 B。

    Returns:
        float: 余弦相似度，范围 [-1, 1]。
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# 知识库定向检索通道（数据库真实检索）
# ---------------------------------------------------------------------------


class KnowledgeBaseChannel(SearchChannel):
    """知识库定向检索通道 —— 使用 pgvector 原生向量检索。

    根据指定的 ``knowledge_base_id``，通过 PostgreSQL pgvector 扩展的
    ``<=>`` 距离操作符直接在数据库中执行向量最近邻搜索，避免将全量
    分块加载到 Python 内存。

    此通道需要 ``AsyncSession`` 和 ``EmbeddingService`` 实例。
    """

    def __init__(
        self,
        knowledge_base_id: int,
        db_session: AsyncSession,
        embedding_service: EmbeddingService,
    ) -> None:
        """初始化知识库检索通道。

        Args:
            knowledge_base_id: 知识库 ID。
            db_session:        异步数据库会话。
            embedding_service: 向量嵌入服务实例（用于对查询文本进行向量化）。
        """
        self._kb_id = knowledge_base_id
        self._db = db_session
        self._embedding = embedding_service
        self._channel_name = f"kb-{knowledge_base_id}"

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[SearchResult]:
        """使用 pgvector 执行向量最近邻检索。

        流程：
            1. 将查询向量转为 pgvector 格式
            2. 使用 ``embedding <=> $query`` 计算余弦距离
            3. 按 distance 升序排列，返回 top_k 条结果

        Args:
            query_embedding: 查询向量（1024 维）。
            top_k:           返回的最大结果数。

        Returns:
            list[SearchResult]: 检索结果列表。
        """
        logger.debug(
            "知识库检索(pgvector): kb_id=%d, top_k=%d",
            self._kb_id,
            top_k,
        )

        # 将 Python list[float] 转为 pgvector 字符串格式
        vector_str = "[" + ", ".join(str(v) for v in query_embedding) + "]"

        # 使用原生 SQL 执行 pgvector 向量搜索
        # <=> 是余弦距离操作符，值越小越相似
        stmt = sa_text("""
            SELECT id, kb_id, doc_id, content, chunk_index,
                   keywords, summary,
                   1 - (embedding <=> :query_vec) AS similarity
            FROM t_knowledge_chunk
            WHERE kb_id = :kb_id
              AND enabled = TRUE
              AND embedding IS NOT NULL
            ORDER BY embedding <=> :query_vec
            LIMIT :limit
        """)

        try:
            result = await self._db.execute(
                stmt,
                {"query_vec": vector_str, "kb_id": self._kb_id, "limit": top_k},
            )
            rows = result.fetchall()
        except Exception:
            logger.warning(
                "pgvector 检索失败, kb_id=%d, 回退到无向量模式",
                self._kb_id,
                exc_info=True,
            )
            return []

        if not rows:
            logger.debug("知识库 %d 无可用分块（embedding）", self._kb_id)
            return []

        # 构建返回结果
        results: list[SearchResult] = []
        for row in rows:
            results.append(
                SearchResult(
                    chunk_id=str(row.id),
                    content=row.content,
                    score=round(float(row.similarity), 4),
                    metadata={
                        "kb_id": row.kb_id,
                        "doc_id": row.doc_id,
                        "chunk_index": row.chunk_index,
                        "keywords": row.keywords,
                        "summary": row.summary,
                        "source": "knowledge_base",
                    },
                    source_channel=self._channel_name,
                )
            )

        logger.debug(
            "知识库检索完成(pgvector): kb_id=%d, 返回=%d",
            self._kb_id,
            len(results),
        )
        return results


# ---------------------------------------------------------------------------
# 后处理器：去重
# ---------------------------------------------------------------------------


class DeduplicatePostProcessor:
    """基于 content_hash 的去重后处理器。

    对检索结果按 content_hash 去重，保留分数最高的条目。
    """

    def process(self, results: list[SearchResult]) -> list[SearchResult]:
        """对检索结果进行去重。

        当多个通道返回相同内容时，仅保留分数最高的结果。

        Args:
            results: 待去重的检索结果列表。

        Returns:
            list[SearchResult]: 去重后的结果列表（保持原顺序）。
        """
        seen: dict[str, SearchResult] = {}

        for result in results:
            h = result.content_hash
            if h not in seen or result.score > seen[h].score:
                seen[h] = result

        deduped = list(seen.values())
        # 按分数降序排列
        deduped.sort(key=lambda r: r.score, reverse=True)

        removed = len(results) - len(deduped)
        if removed > 0:
            logger.debug("去重: 移除 %d 条重复结果", removed)

        return deduped


# ---------------------------------------------------------------------------
# 后处理器：重排序（接口预留）
# ---------------------------------------------------------------------------


class RerankPostProcessor:
    """重排序后处理器。

    当前为 Mock 实现，直接返回原始结果。
    实际部署时可接入 LLM 或专用 Rerank 模型（如 Cohere Rerank）。
    """

    def __init__(self, llm_service: Any = None) -> None:
        """初始化重排序后处理器。

        Args:
            llm_service: LLM 服务实例（预留，用于基于 LLM 的重排序）。
        """
        self._llm_service = llm_service

    async def process(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int = 10,
    ) -> list[SearchResult]:
        """对检索结果进行重排序。

        当前 Mock 实现：直接返回原始结果（截取 top_k）。
        实际实现可使用 LLM 对每个结果的相关性进行评分和重排。

        Args:
            query:   原始查询文本。
            results: 待重排序的结果列表。
            top_k:   返回的最大结果数。

        Returns:
            list[SearchResult]: 重排序后的结果列表。
        """
        # TODO: 接入实际 Rerank 模型
        logger.debug("重排序(Mock): query='%s', 输入 %d 条结果", query, len(results))
        return results[:top_k]


# ---------------------------------------------------------------------------
# 多路检索引擎
# ---------------------------------------------------------------------------


class RetrievalEngine:
    """多路检索引擎 —— 编排多通道并行检索与后处理。

    使用方式::

        from ragent.infra.ai.embedding_service import EmbeddingService

        engine = RetrievalEngine(embedding_service)
        results = await engine.search("什么是RAG？", intent=intent_node, top_k=10)
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        dedup_processor: DeduplicatePostProcessor | None = None,
        rerank_processor: RerankPostProcessor | None = None,
        db_session: AsyncSession | None = None,
    ) -> None:
        """初始化检索引擎。

        Args:
            embedding_service: 向量嵌入服务实例。
            dedup_processor:   去重后处理器，若为 ``None`` 则使用默认实例。
            rerank_processor:  重排序后处理器，若为 ``None`` 则使用默认实例。
            db_session:        异步数据库会话，用于知识库定向检索。
        """
        self._embedding = embedding_service
        self._dedup = dedup_processor or DeduplicatePostProcessor()
        self._rerank = rerank_processor or RerankPostProcessor()
        self._db_session = db_session

    async def search(
        self,
        query: str,
        intent: IntentNode | None = None,
        top_k: int = 10,
        knowledge_base_id: int | None = None,
    ) -> list[SearchResult]:
        """执行多路检索。

        处理步骤：
            1. 将查询文本向量化
            2. 根据意图和知识库 ID 构建检索通道列表
            3. 并行执行所有通道检索
            4. 后处理：去重 → 重排序
            5. 返回 top_k 结果

        Args:
            query:             查询文本。
            intent:            意图节点，若为 ``None`` 则执行全局搜索。
            top_k:             返回的最大结果数。
            knowledge_base_id: 知识库 ID，若指定则限定检索范围。

        Returns:
            list[SearchResult]: 检索结果列表。
        """
        logger.debug(
            "检索引擎: query='%s', intent=%s, top_k=%d, kb_id=%s",
            query, intent, top_k, knowledge_base_id,
        )

        # 步骤 1：向量化
        try:
            query_embedding = await self._embedding.embed(query)
        except Exception:
            logger.error("查询向量化失败", exc_info=True)
            return []

        # 步骤 2：构建检索通道
        channels = self._build_channels(intent, knowledge_base_id=knowledge_base_id)

        if not channels:
            return []

        # 步骤 3：并行检索
        search_tasks = [channel.search(query_embedding, top_k=top_k) for channel in channels]
        channel_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # 收集成功的结果
        all_results: list[SearchResult] = []
        for i, result in enumerate(channel_results):
            if isinstance(result, Exception):
                logger.warning("通道 %s 检索失败: %s", channels[i].__class__.__name__, result)
                continue
            all_results.extend(result)

        logger.debug("检索引擎: 原始结果 %d 条", len(all_results))

        # 步骤 4：后处理
        # 去重
        all_results = self._dedup.process(all_results)

        # 重排序
        all_results = await self._rerank.process(query, all_results, top_k=top_k)

        logger.debug("检索引擎: 最终结果 %d 条", len(all_results))
        return all_results

    # ------------------------------------------------------------------ #
    # 通道构建
    # ------------------------------------------------------------------ #

    def _build_channels(
        self,
        intent: IntentNode | None,
        *,
        knowledge_base_id: int | None = None,
    ) -> list[SearchChannel]:
        """根据意图和知识库 ID 构建检索通道列表。

        策略：
            - 指定了 knowledge_base_id：优先使用知识库定向检索通道（真实 DB 查询）
            - 意图明确：构建意图定向通道 + 全局通道作为补充
            - 意图不明确：仅使用全局通道

        Args:
            intent:            意图节点。
            knowledge_base_id: 知识库 ID，若指定则添加知识库定向通道。

        Returns:
            list[SearchChannel]: 检索通道列表。
        """
        channels: list[SearchChannel] = []

        # 指定知识库时，添加真实 DB 检索通道
        if knowledge_base_id is not None and self._db_session is not None:
            channels.append(
                KnowledgeBaseChannel(
                    knowledge_base_id=knowledge_base_id,
                    db_session=self._db_session,
                    embedding_service=self._embedding,
                )
            )

        if intent is not None and intent.collection_name:
            # 意图明确：定向检索
            channels.append(IntentDirectedChannel(intent))

        # 始终添加全局通道作为兜底
        channels.append(GlobalVectorChannel())

        return channels
