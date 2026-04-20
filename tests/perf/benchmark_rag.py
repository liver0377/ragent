"""
RAG 管线性能基准测试。

测试场景：
    1. test_benchmark_query_rewrite — 查询重写性能（mock LLM，测量纯处理时间）
    2. test_benchmark_intent_classify — 意图分类性能（mock LLM）
    3. test_benchmark_retrieval — 检索引擎性能（mock embedding）
    4. test_benchmark_prompt_build — Prompt 构建性能
    5. test_benchmark_session_memory — 会话记忆 CRUD 性能（添加 N 条消息、获取记忆、摘要）
    6. test_benchmark_full_rag_chain — 完整 RAG 管线性能（mock 所有外部调用）

使用 time.perf_counter() 精确计时。每个测试输出：
    - 单次执行时间
    - 多次执行的平均时间（至少 100 次迭代）
    - P50/P95/P99 延迟
    - 吞吐量（ops/sec）
"""

from __future__ import annotations

import asyncio
import statistics
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragent.infra.ai.llm_service import LLMService
from ragent.infra.ai.model_selector import ModelSelector
from ragent.infra.ai.embedding_service import EmbeddingService
from ragent.rag.chain import RAGChain, MOCK_INTENT_TREE
from ragent.rag.intent.intent_classifier import IntentClassifier, IntentNode
from ragent.rag.memory.session_memory import SessionMemoryManager
from ragent.rag.prompt.prompt_builder import PromptBuilder
from ragent.rag.retrieval.retriever import RetrievalEngine, SearchResult
from ragent.rag.rewriter.query_rewriter import QueryRewriter
from ragent.common.sse import SSEEvent


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

def _stats(durations: list[float]) -> dict:
    """根据耗时列表计算统计信息。"""
    sorted_d = sorted(durations)
    n = len(sorted_d)
    total = sum(sorted_d)
    avg = total / n
    p50 = sorted_d[int(n * 0.50)]
    p95 = sorted_d[int(n * 0.95)]
    p99 = sorted_d[int(n * 0.99)]
    ops = n / total if total > 0 else 0
    return {
        "avg_ms": avg * 1000,
        "p50_ms": p50 * 1000,
        "p95_ms": p95 * 1000,
        "p99_ms": p99 * 1000,
        "ops_sec": ops,
        "total_s": total,
    }


def _print_stats(label: str, single_s: float, stats: dict) -> None:
    """打印性能统计。"""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  单次执行时间:    {single_s * 1000:.4f} ms")
    print(f"  平均时间:        {stats['avg_ms']:.4f} ms")
    print(f"  P50 延迟:        {stats['p50_ms']:.4f} ms")
    print(f"  P95 延迟:        {stats['p95_ms']:.4f} ms")
    print(f"  P99 延迟:        {stats['p99_ms']:.4f} ms")
    print(f"  吞吐量:          {stats['ops_sec']:.1f} ops/sec")
    print(f"  总耗时:          {stats['total_s']:.4f} s")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _mock_llm_service():
    """创建 mock LLMService。"""
    llm = AsyncMock(spec=LLMService)
    llm.chat = AsyncMock(return_value='{"rewritten": "测试查询", "sub_questions": [], "normalized_terms": {}}')
    llm.stream_chat = AsyncMock()
    return llm


@pytest.fixture()
def _mock_embedding_service():
    """创建 mock EmbeddingService。"""
    emb = AsyncMock(spec=EmbeddingService)
    emb.embed = AsyncMock(return_value=[0.1] * 768)
    emb.embed_batch = AsyncMock(return_value=[[0.1] * 768])
    return emb


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_benchmark_query_rewrite(_mock_llm_service):
    """查询重写性能（mock LLM，测量纯处理时间）。"""
    rewriter = QueryRewriter(_mock_llm_service)

    # ---- 预热 ----
    await rewriter.rewrite("什么是 RAG？", [])

    # ---- 单次执行 ----
    t0 = time.perf_counter()
    result = await rewriter.rewrite("什么是 RAG 技术？请详细解释", [])
    single = time.perf_counter() - t0
    assert result is not None

    # ---- 批量执行 ----
    N = 100
    durations: list[float] = []
    questions = [
        "什么是 RAG？",
        "如何使用向量数据库？",
        "大语言模型有哪些应用？",
        "请解释 Embedding 的作用",
        "检索增强生成的原理是什么？",
    ]
    for i in range(N):
        q = questions[i % len(questions)]
        t0 = time.perf_counter()
        await rewriter.rewrite(q, [])
        durations.append(time.perf_counter() - t0)

    s = _stats(durations)
    _print_stats("查询重写 (Query Rewrite)", single, s)
    assert s["avg_ms"] > 0


@pytest.mark.asyncio
async def test_benchmark_intent_classify(_mock_llm_service):
    """意图分类性能（mock LLM）。"""
    classifier = IntentClassifier(_mock_llm_service)
    intent_tree = MOCK_INTENT_TREE

    # 让 LLM 返回合理的意图分数
    _mock_llm_service.chat = AsyncMock(
        return_value='{"intent": "TOPIC_RAG", "confidence": 0.9, "candidates": [{"code": "TOPIC_RAG", "score": 0.9}]}'
    )

    # ---- 预热 ----
    await classifier.classify("什么是 RAG？", intent_tree)

    # ---- 单次执行 ----
    t0 = time.perf_counter()
    result = await classifier.classify("什么是 RAG？", intent_tree)
    single = time.perf_counter() - t0
    assert result is not None

    # ---- 批量执行 ----
    N = 100
    durations: list[float] = []
    queries = [
        "什么是 RAG？",
        "如何使用 LLM？",
        "向量嵌入的原理",
        "产品功能有哪些？",
        "知识库怎么搭建？",
    ]
    for i in range(N):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        await classifier.classify(q, intent_tree)
        durations.append(time.perf_counter() - t0)

    s = _stats(durations)
    _print_stats("意图分类 (Intent Classification)", single, s)
    assert s["avg_ms"] > 0


@pytest.mark.asyncio
async def test_benchmark_retrieval(_mock_embedding_service):
    """检索引擎性能（mock embedding）。"""
    engine = RetrievalEngine(_mock_embedding_service)
    intent = IntentNode(
        intent_code="TOPIC_RAG",
        name="RAG 知识",
        level=2,
        parent_code="DOMAIN_TECH",
        examples=["RAG", "检索增强"],
        collection_name="rag_knowledge",
        kind=0,
    )

    # ---- 预热 ----
    await engine.search("什么是 RAG？", intent=intent, top_k=5)

    # ---- 单次执行 ----
    t0 = time.perf_counter()
    results = await engine.search("什么是 RAG？", intent=intent, top_k=5)
    single = time.perf_counter() - t0
    assert isinstance(results, list)

    # ---- 批量执行 ----
    N = 100
    durations: list[float] = []
    queries = [
        "什么是 RAG？",
        "如何进行语义搜索？",
        "向量检索的原理是什么？",
        "Milvus 和 pgvector 的区别",
        "混合检索策略有哪些？",
    ]
    for i in range(N):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        await engine.search(q, intent=intent, top_k=5)
        durations.append(time.perf_counter() - t0)

    s = _stats(durations)
    _print_stats("检索引擎 (Retrieval Engine)", single, s)
    assert s["avg_ms"] > 0


@pytest.mark.asyncio
async def test_benchmark_prompt_build():
    """Prompt 构建性能。"""
    builder = PromptBuilder()

    messages = [
        {"role": "user", "content": "什么是 RAG？"},
        {"role": "assistant", "content": "RAG 是检索增强生成技术。"},
        {"role": "user", "content": "请详细介绍"},
    ]
    context_results = [
        SearchResult(
            chunk_id=f"chunk_{i}",
            content=f"这是第 {i} 条检索结果内容，包含关于 RAG 技术的详细信息。" * 5,
            score=0.95 - i * 0.05,
            metadata={"source": f"doc_{i}"},
            source_channel="global_vector",
        )
        for i in range(5)
    ]
    history = [
        {"role": "user", "content": f"历史问题 {i}"} for i in range(10)
    ]

    # 预计算上下文字符串（与 RAGChain._build_context 逻辑一致）
    context_str = "\n".join(
        f"[{i + 1}] {r.content}" for i, r in enumerate(context_results)
    )

    # ---- 预热 ----
    await builder.build(
        messages=messages,
        context=context_str,
        history=history,
    )

    # ---- 单次执行 ----
    t0 = time.perf_counter()
    result = await builder.build(
        messages=messages,
        context=context_str,
        history=history,
    )
    single = time.perf_counter() - t0
    assert result is not None

    # ---- 批量执行 ----
    N = 100
    durations: list[float] = []
    for _ in range(N):
        t0 = time.perf_counter()
        await builder.build(
            messages=messages,
            context=context_str,
            history=history,
        )
        durations.append(time.perf_counter() - t0)

    s = _stats(durations)
    _print_stats("Prompt 构建 (Prompt Builder)", single, s)
    assert s["avg_ms"] > 0


@pytest.mark.asyncio
async def test_benchmark_session_memory(_mock_llm_service):
    """会话记忆 CRUD 性能（添加 N 条消息、获取记忆、摘要）。"""
    memory_mgr = SessionMemoryManager(_mock_llm_service, window_size=20)
    conversation_id = 90001

    # ---- 测试 1: 添加消息性能 ----
    N_ADD = 100
    t0 = time.perf_counter()
    for j in range(N_ADD):
        await memory_mgr.add_message(conversation_id, "user", f"消息 {j}")
    add_total = time.perf_counter() - t0
    add_per_msg = add_total / N_ADD

    # ---- 测试 2: 获取记忆性能 ----
    N_GET = 100
    durations_get: list[float] = []
    for _ in range(N_GET):
        t0 = time.perf_counter()
        await memory_mgr.get_memory(conversation_id)
        durations_get.append(time.perf_counter() - t0)
    get_stats = _stats(durations_get)

    # ---- 测试 3: 摘要性能 (mock LLM) ----
    _mock_llm_service.chat = AsyncMock(return_value="这是会话摘要内容。")
    cid2 = 90002
    for j in range(25):
        await memory_mgr.add_message(cid2, "user" if j % 2 == 0 else "assistant", f"对话消息 {j}")

    N_SUM = 100
    durations_sum: list[float] = []
    for _ in range(N_SUM):
        t0 = time.perf_counter()
        await memory_mgr.summarize(cid2)
        durations_sum.append(time.perf_counter() - t0)
    sum_stats = _stats(durations_sum)

    # ---- 批量综合测试 ----
    N_FULL = 100
    durations_full: list[float] = []
    for i in range(N_FULL):
        cid = 91000 + i
        t0 = time.perf_counter()
        await memory_mgr.add_message(cid, "user", f"测试消息 {i}")
        await memory_mgr.get_memory(cid)
        durations_full.append(time.perf_counter() - t0)
    full_stats = _stats(durations_full)

    print(f"\n{'=' * 60}")
    print(f"  会话记忆 (Session Memory) CRUD 性能")
    print(f"{'=' * 60}")
    print(f"  添加消息:  {add_per_msg * 1000:.4f} ms/msg  ({N_ADD / add_total:.1f} ops/sec)")
    print(f"  获取记忆:  avg={get_stats['avg_ms']:.4f} ms  P50={get_stats['p50_ms']:.4f} ms  P95={get_stats['p95_ms']:.4f} ms")
    print(f"  摘要生成:  avg={sum_stats['avg_ms']:.4f} ms  P50={sum_stats['p50_ms']:.4f} ms  P95={sum_stats['p95_ms']:.4f} ms")
    print(f"  综合 CRUD: avg={full_stats['avg_ms']:.4f} ms  P50={full_stats['p50_ms']:.4f} ms  P95={full_stats['p95_ms']:.4f} ms")
    print(f"  综合吞吐:  {full_stats['ops_sec']:.1f} ops/sec")
    print(f"{'=' * 60}")

    assert add_per_msg > 0
    assert get_stats["avg_ms"] > 0
    assert sum_stats["avg_ms"] > 0


@pytest.mark.asyncio
async def test_benchmark_full_rag_chain(_mock_llm_service, _mock_embedding_service):
    """完整 RAG 管线性能（mock 所有外部调用）。"""
    # 设置 mock 返回值
    _mock_llm_service.chat = AsyncMock(
        return_value='{"rewritten": "什么是 RAG？", "sub_questions": [], "normalized_terms": {}}'
    )

    # stream_chat 需要返回异步生成器
    async def _mock_stream(*args, **kwargs):
        for token in ["RAG是", "检索增强", "生成技术"]:
            yield token

    _mock_llm_service.stream_chat = _mock_stream

    chain = RAGChain(
        llm_service=_mock_llm_service,
        embedding_service=_mock_embedding_service,
    )

    # ---- 预热 ----
    events = []
    async for event in chain.ask("什么是 RAG？", conversation_id=80001, user_id=1):
        events.append(event)

    # ---- 单次执行 ----
    t0 = time.perf_counter()
    events = []
    async for event in chain.ask("什么是 RAG？", conversation_id=80002, user_id=1):
        events.append(event)
    single = time.perf_counter() - t0

    # ---- 批量执行 ----
    N = 100
    durations: list[float] = []
    queries = [
        "什么是 RAG？",
        "如何使用向量数据库？",
        "大语言模型有哪些应用？",
        "请解释 Embedding 的作用",
        "检索增强生成的原理是什么？",
    ]
    for i in range(N):
        q = queries[i % len(queries)]
        cid = 81000 + i
        t0 = time.perf_counter()
        async for _ in chain.ask(q, conversation_id=cid, user_id=1):
            pass
        durations.append(time.perf_counter() - t0)

    s = _stats(durations)
    _print_stats("完整 RAG 管线 (Full RAG Chain)", single, s)
    assert s["avg_ms"] > 0
