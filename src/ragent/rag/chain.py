"""RAG 问答主链路 —— 编排完整的 RAG 管线。

核心职责：
    1. 接收用户问题，编排完整的 RAG 流程
    2. 依次执行：查询重写 → 意图分类 → 检索 → Prompt 组装 → 流式生成
    3. 通过 SSE 事件流式输出中间状态和最终结果
    4. 会话记忆管理

管线阶段：
    query-rewrite → intent-classify → retrieval → prompt-build → llm-generate
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ragent.common.sse import SSEEvent, SSEEventType, sse_content, sse_error, sse_finish, sse_meta
from ragent.common.trace import TraceSpan, _trace_context, _trace_id_var, _log_span
from ragent.infra.ai.embedding_service import EmbeddingService
from ragent.infra.ai.llm_service import LLMService
from ragent.rag.intent.intent_classifier import IntentClassifier, IntentNode
from ragent.rag.memory.session_memory import SessionMemoryManager
from ragent.rag.prompt.prompt_builder import PromptBuilder
from ragent.rag.retrieval.retriever import RetrievalEngine, SearchResult
from ragent.rag.rewriter.query_rewriter import QueryRewriter
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 默认 Mock 意图树
# ---------------------------------------------------------------------------

MOCK_INTENT_TREE: list[IntentNode] = [
    # 领域层（level 1）
    IntentNode(
        intent_code="DOMAIN_TECH",
        name="技术",
        level=1,
        parent_code=None,
    ),
    IntentNode(
        intent_code="DOMAIN_BIZ",
        name="业务",
        level=1,
        parent_code=None,
    ),
    # 主题层（level 2，叶节点）
    IntentNode(
        intent_code="TOPIC_RAG",
        name="RAG检索增强生成",
        level=2,
        parent_code="DOMAIN_TECH",
        examples=["什么是RAG", "检索增强生成", "RAG的原理", "RAG和微调的区别"],
        collection_name="rag_knowledge",
        kind=0,
    ),
    IntentNode(
        intent_code="TOPIC_LLM",
        name="大语言模型",
        level=2,
        parent_code="DOMAIN_TECH",
        examples=["什么是LLM", "大语言模型的应用", "GPT的原理", "Transformer架构"],
        collection_name="llm_knowledge",
        kind=0,
    ),
    IntentNode(
        intent_code="TOPIC_EMB",
        name="向量嵌入",
        level=2,
        parent_code="DOMAIN_TECH",
        examples=["什么是Embedding", "向量嵌入", "文本向量化", "嵌入模型"],
        collection_name="embedding_knowledge",
        kind=0,
    ),
    IntentNode(
        intent_code="TOPIC_PRODUCT",
        name="产品介绍",
        level=2,
        parent_code="DOMAIN_BIZ",
        examples=["产品有哪些功能", "产品介绍", "怎么使用"],
        collection_name="product_knowledge",
        kind=0,
    ),
]


# ---------------------------------------------------------------------------
# RAG 链路
# ---------------------------------------------------------------------------


class RAGChain:
    """RAG 问答主链路 —— 编排完整的 RAG 管线。

    使用方式::

        chain = RAGChain(llm_service, embedding_service)
        async for event in chain.ask("什么是RAG？"):
            print(event)
    """

    def __init__(
        self,
        llm_service: LLMService,
        embedding_service: EmbeddingService,
        intent_tree: list[IntentNode] | None = None,
        *,
        window_size: int = 20,
    ) -> None:
        """初始化 RAG 链路。

        Args:
            llm_service:       LLM 服务实例。
            embedding_service: 向量嵌入服务实例。
            intent_tree:       意图树节点列表，若为 ``None`` 则使用 Mock 数据。
            window_size:       会话记忆窗口大小。
        """
        self._llm = llm_service
        self._embedding = embedding_service
        self._intent_tree = intent_tree or MOCK_INTENT_TREE

        # 初始化子模块
        self._rewriter = QueryRewriter(llm_service)
        self._classifier = IntentClassifier(llm_service)
        self._retriever = RetrievalEngine(embedding_service)
        self._memory = SessionMemoryManager(llm_service, window_size=window_size)
        self._prompt_builder = PromptBuilder()

    async def ask(
        self,
        question: str,
        conversation_id: int | None = None,
        user_id: int | None = None,
        db_session: AsyncSession | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """执行完整的 RAG 问答管线。

        处理步骤：
            1. 发送队列状态事件
            2. 查询重写
            3. 意图分类
            4. 检索
            5. Prompt 组装
            6. 流式生成
            7. 保存到记忆

        注意：此方法为异步生成器，使用 ``async for`` 迭代。

        Args:
            question:        用户问题。
            conversation_id: 会话 ID，若为 ``None`` 则不保存记忆。
            user_id:         用户 ID（可选）。
            db_session:      异步数据库会话，用于会话持久化。

        Yields:
            SSEEvent: SSE 事件流。
        """
        # 注入数据库会话到 memory manager
        if db_session is not None:
            self._memory.set_db(db_session)
        # 手动创建根追踪段（async generator 不兼容装饰器）
        root_span = TraceSpan(name="rag-pipeline")
        trace_id = uuid.uuid4().hex
        _trace_id_var.set(trace_id)
        ctx_token = _trace_context.set(root_span)

        logger.info("RAG 链路开始: question='%s', conv_id=%s", question, conversation_id)

        try:
            # 步骤 1：发送 meta 事件
            yield sse_meta({
                "status": "processing",
                "conversation_id": conversation_id,
            })

            # 加载历史记忆
            history: list[dict[str, str]] = []
            if conversation_id is not None:
                memory = await self._memory.get_memory(conversation_id)
                if memory.summary:
                    history.append({"role": "assistant", "content": f"[对话摘要] {memory.summary}"})
                for msg in memory.recent_messages:
                    history.append({"role": msg.role, "content": msg.content})

            # 步骤 2：查询重写
            yield sse_meta({"stage": "query-rewrite"})
            rewrite_result = await self._rewrite_step(question, history)
            rewritten_query = rewrite_result.rewritten if rewrite_result else question

            # 步骤 3：意图分类
            yield sse_meta({"stage": "intent-classify"})
            intent_result = await self._classify_step(rewritten_query)

            # 步骤 4：检索
            yield sse_meta({"stage": "retrieval"})
            search_results = await self._retrieval_step(
                rewritten_query, intent_result.intent if intent_result else None
            )

            # 步骤 5：Prompt 组装
            yield sse_meta({"stage": "prompt-build"})
            context = self._build_context(search_results)
            current_messages = [{"role": "user", "content": question}]

            # 获取意图相关的自定义提示词
            system_prompt = None
            rag_prompt = None
            if intent_result and intent_result.intent:
                system_prompt = intent_result.intent.system_prompt
                rag_prompt = intent_result.intent.rag_prompt

            llm_messages = await self._prompt_builder.build(
                messages=current_messages,
                context=context,
                history=history[-6:] if history else None,  # 最近 3 轮
                system_prompt=system_prompt,
                rag_prompt=rag_prompt,
            )

            # 步骤 6：流式生成
            yield sse_meta({"stage": "llm-generate"})
            full_response = ""

            async for token in self._generate_step(llm_messages):
                if isinstance(token, SSEEvent):
                    yield token
                else:
                    full_response += token
                    yield sse_content(token)

            # 步骤 7：保存到记忆
            if conversation_id is not None:
                await self._save_memory(conversation_id, question, full_response, user_id=user_id)

            # 步骤 8：发送结束事件
            yield sse_finish({
                "conversation_id": conversation_id,
                "intent": intent_result.intent.name if intent_result and intent_result.intent else None,
                "confidence": intent_result.confidence if intent_result else 0.0,
                "result_count": len(search_results),
            })

            root_span.finish(status="ok")
        except Exception as exc:
            logger.error("RAG 链路异常: %s", exc, exc_info=True)
            root_span.finish(status="error", error_message=str(exc))
            yield sse_error(message=str(exc), code="B2001")
        finally:
            _log_span(root_span, is_root=True)
            _trace_context.reset(ctx_token)

    # ------------------------------------------------------------------ #
    # 子步骤
    # ------------------------------------------------------------------ #

    async def _rewrite_step(
        self,
        question: str,
        history: list[dict[str, str]],
    ) -> Any:
        """查询重写步骤。

        Args:
            question: 用户原始问题。
            history:  对话历史。

        Returns:
            RewriteResult | None: 重写结果。
        """
        # 创建子追踪段
        span = TraceSpan(name="query-rewrite")
        parent = _trace_context.get(None)
        if parent:
            parent.children.append(span)

        try:
            result = await self._rewriter.rewrite(question, history=history if history else None)
            logger.debug("查询重写: '%s' -> '%s'", question, result.rewritten)
            span.finish(status="ok")
            return result
        except Exception as exc:
            span.finish(status="error", error_message=str(exc))
            logger.warning("查询重写失败，使用原始问题", exc_info=True)
            return None

    async def _classify_step(self, query: str) -> Any:
        """意图分类步骤。

        Args:
            query: 重写后的查询。

        Returns:
            IntentResult | None: 分类结果。
        """
        span = TraceSpan(name="intent-classify")
        parent = _trace_context.get(None)
        if parent:
            parent.children.append(span)

        try:
            result = await self._classifier.classify(query, self._intent_tree)
            logger.debug(
                "意图分类: intent=%s, confidence=%.2f",
                result.intent.name if result.intent else "None",
                result.confidence,
            )
            span.finish(status="ok")
            return result
        except Exception as exc:
            span.finish(status="error", error_message=str(exc))
            logger.warning("意图分类失败", exc_info=True)
            return None

    async def _retrieval_step(
        self,
        query: str,
        intent: IntentNode | None,
    ) -> list[SearchResult]:
        """检索步骤。

        Args:
            query:  查询文本。
            intent: 意图节点。

        Returns:
            list[SearchResult]: 检索结果列表。
        """
        span = TraceSpan(name="retrieval")
        parent = _trace_context.get(None)
        if parent:
            parent.children.append(span)

        try:
            results = await self._retriever.search(query, intent=intent)
            logger.debug("检索完成: %d 条结果", len(results))
            span.finish(status="ok")
            return results
        except Exception as exc:
            span.finish(status="error", error_message=str(exc))
            logger.warning("检索失败", exc_info=True)
            return []

    async def _generate_step(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str | SSEEvent]:
        """流式生成步骤。

        Args:
            messages: LLM 消息列表。

        Yields:
            str | SSEEvent: 生成的内容片段或 SSE 事件。
        """
        try:
            async for token in self._llm.stream_chat(messages):
                yield token
        except Exception as exc:
            logger.error("LLM 生成失败: %s", exc, exc_info=True)
            yield sse_error(message=f"生成失败: {exc}", code="C3001")

    async def _save_memory(
        self,
        conversation_id: int,
        question: str,
        answer: str,
        user_id: int | None = None,
    ) -> None:
        """保存消息到会话记忆。

        Args:
            conversation_id: 会话 ID。
            question:        用户问题。
            answer:          助手回答。
            user_id:         用户 ID。
        """
        try:
            await self._memory.add_message(conversation_id, "user", question, user_id=user_id)
            await self._memory.add_message(conversation_id, "assistant", answer, user_id=user_id)

            # 检查是否需要摘要
            if await self._memory.should_summarize(conversation_id):
                await self._memory.summarize(conversation_id)

        except Exception:
            logger.warning("保存记忆失败", exc_info=True)

    # ------------------------------------------------------------------ #
    # 辅助方法
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_context(results: list[SearchResult]) -> str | None:
        """将检索结果构建为上下文文本。

        Args:
            results: 检索结果列表。

        Returns:
            str | None: 上下文文本，若无结果则为 ``None``。
        """
        if not results:
            return None

        chunks: list[str] = []
        for i, r in enumerate(results):
            chunks.append(f"[{i + 1}] {r.content}")

        return "\n".join(chunks)
