"""测试问题重写器 —— QueryRewriter 单元测试。

覆盖场景：
    - 无历史对话时直接返回（含归一化）
    - 有历史对话时的上下文补全
    - 关键词归一化
    - 复杂问题拆分
    - LLM 调用失败时的降级处理
"""

from __future__ import annotations

import json
import pytest

from ragent.rag.rewriter.query_rewriter import QueryRewriter, RewriteResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockLLMService:
    """模拟 LLM 服务。"""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or []
        self._call_count = 0
        self._last_messages: list[dict] | None = None

    async def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        self._last_messages = messages
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        self._call_count += 1
        return ""

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def last_messages(self) -> list[dict] | None:
        return self._last_messages


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_no_history():
    """无对话历史时，应直接进行归一化（跳过上下文补全 LLM 调用）。"""
    llm = MockLLMService(responses=['[]'])
    rewriter = QueryRewriter(llm)

    result = await rewriter.rewrite("什么是RAG技术？")

    assert isinstance(result, RewriteResult)
    # RAG 应该被归一化
    assert "检索增强生成" in result.rewritten or "RAG" in result.rewritten
    # 无历史时，上下文补全不应调用 LLM
    assert llm.call_count == 1  # 只有拆分调用


@pytest.mark.asyncio
async def test_rewrite_with_history():
    """有对话历史时，应进行上下文补全。"""
    llm = MockLLMService(responses=[
        "检索增强生成是什么？",  # 上下文补全
        '[]',  # 拆分（空列表）
    ])
    rewriter = QueryRewriter(llm)

    history = [
        {"role": "user", "content": "介绍一下RAG"},
        {"role": "assistant", "content": "RAG是检索增强生成..."},
    ]
    result = await rewriter.rewrite("它有什么优势？", history=history)

    assert result.rewritten == "检索增强生成是什么？"
    assert llm.call_count == 2  # 补全 + 拆分


@pytest.mark.asyncio
async def test_normalize_terms():
    """关键词归一化应正确映射同义词。"""
    llm = MockLLMService(responses=['[]'])
    rewriter = QueryRewriter(llm)

    result = await rewriter.rewrite("AI和ML有什么区别？")

    # 检查归一化映射
    assert "AI" in result.normalized_terms or "ai" in result.normalized_terms
    assert "ML" in result.normalized_terms or "ml" in result.normalized_terms


@pytest.mark.asyncio
async def test_normalize_custom_mapping():
    """自定义同义词映射表应生效。"""
    llm = MockLLMService(responses=['[]'])
    custom_mapping = {"数据库": "关系型数据库", "缓存": "Redis缓存"}
    rewriter = QueryRewriter(llm, term_mapping=custom_mapping)

    result = await rewriter.rewrite("数据库和缓存的区别？")

    assert "数据库" in result.normalized_terms
    assert result.normalized_terms["数据库"] == "关系型数据库"


@pytest.mark.asyncio
async def test_split_complex_question():
    """复杂问题应被拆分为子问题。"""
    llm = MockLLMService(responses=[
        '["什么是RAG？", "RAG有哪些应用场景？"]',  # 拆分
    ])
    rewriter = QueryRewriter(llm, enable_split=True)

    result = await rewriter.rewrite("什么是RAG？它有哪些应用场景？")

    assert len(result.sub_questions) == 2
    assert "RAG" in result.sub_questions[0]


@pytest.mark.asyncio
async def test_split_simple_question():
    """简单问题不应被拆分。"""
    llm = MockLLMService(responses=['[]'])
    rewriter = QueryRewriter(llm, enable_split=True)

    result = await rewriter.rewrite("什么是RAG？")

    assert len(result.sub_questions) == 0


@pytest.mark.asyncio
async def test_rewrite_llm_failure_graceful():
    """LLM 调用失败时应优雅降级。归一化仍会生效。"""
    class FailingLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("LLM 不可用")

    rewriter = QueryRewriter(FailingLLM())

    result = await rewriter.rewrite("什么是AI？")

    # 应该优雅降级 — 归一化会将 AI 替换为 人工智能
    assert isinstance(result, RewriteResult)
    assert "人工智能" in result.rewritten or "AI" in result.rewritten


@pytest.mark.asyncio
async def test_rewrite_llm_failure_preserves_original_without_mapping():
    """LLM 失败且无归一化映射时，返回原始问题。"""
    class FailingLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("LLM 不可用")

    rewriter = QueryRewriter(FailingLLM(), term_mapping={})

    result = await rewriter.rewrite("什么是量子计算？")

    # 无映射，LLM 失败，应返回原始问题
    assert result.rewritten == "什么是量子计算？"


@pytest.mark.asyncio
async def test_split_disabled():
    """禁用拆分时不应调用拆分。"""
    llm = MockLLMService(responses=['["子问题1", "子问题2"]'])
    rewriter = QueryRewriter(llm, enable_split=False)

    result = await rewriter.rewrite("什么是AI？")

    assert result.sub_questions == []
    assert llm.call_count == 0  # 无历史，不调用 LLM
