"""测试意图分类器 —— IntentClassifier 单元测试。

覆盖场景：
    - 无意图树时返回空结果
    - 高置信度直接命中
    - 中等置信度无歧义
    - 歧义检测（Top-1 和 Top-2 分数接近）
    - 低置信度进入全局搜索
    - LLM 返回格式异常时的降级
"""

from __future__ import annotations

import json
import pytest

from ragent.rag.intent.intent_classifier import IntentClassifier, IntentNode, IntentResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_leaf_nodes() -> list[IntentNode]:
    """创建测试用的意图树。"""
    return [
        IntentNode(
            intent_code="TOPIC_RAG",
            name="RAG检索增强生成",
            level=2,
            parent_code="DOMAIN_TECH",
            examples=["什么是RAG", "检索增强生成"],
            collection_name="rag_kb",
        ),
        IntentNode(
            intent_code="TOPIC_LLM",
            name="大语言模型",
            level=2,
            parent_code="DOMAIN_TECH",
            examples=["什么是LLM", "GPT的原理"],
            collection_name="llm_kb",
        ),
        IntentNode(
            intent_code="TOPIC_EMB",
            name="向量嵌入",
            level=2,
            parent_code="DOMAIN_TECH",
            examples=["什么是Embedding", "文本向量化"],
            collection_name="emb_kb",
        ),
        IntentNode(
            intent_code="DOMAIN_TECH",
            name="技术",
            level=1,
            parent_code=None,
        ),
    ]


class MockLLMService:
    """模拟 LLM 服务。"""

    def __init__(self, response: str = "") -> None:
        self._response = response

    async def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        return self._response


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_empty_tree():
    """空意图树应返回空结果。"""
    llm = MockLLMService()
    classifier = IntentClassifier(llm)

    result = await classifier.classify("测试问题", [])

    assert result.intent is None
    assert result.confidence == 0.0
    assert result.candidates == []
    assert not result.needs_clarification


@pytest.mark.asyncio
async def test_classify_high_confidence():
    """高置信度（≥0.8）应直接命中。"""
    llm_response = json.dumps([
        {"code": "TOPIC_RAG", "score": 0.95},
        {"code": "TOPIC_LLM", "score": 0.3},
        {"code": "TOPIC_EMB", "score": 0.1},
    ])
    llm = MockLLMService(response=llm_response)
    classifier = IntentClassifier(llm, high_threshold=0.8)
    nodes = make_leaf_nodes()

    result = await classifier.classify("什么是RAG？", nodes)

    assert result.intent is not None
    assert result.intent.intent_code == "TOPIC_RAG"
    assert result.confidence == 0.95
    assert not result.needs_clarification


@pytest.mark.asyncio
async def test_classify_medium_confidence_no_ambiguity():
    """中等置信度且无歧义时应接受 Top-1。"""
    llm_response = json.dumps([
        {"code": "TOPIC_RAG", "score": 0.7},
        {"code": "TOPIC_LLM", "score": 0.3},
        {"code": "TOPIC_EMB", "score": 0.1},
    ])
    llm = MockLLMService(response=llm_response)
    classifier = IntentClassifier(llm, high_threshold=0.8, low_threshold=0.5, gap_threshold=0.15)
    nodes = make_leaf_nodes()

    result = await classifier.classify("讲讲RAG相关的内容", nodes)

    assert result.intent is not None
    assert result.intent.intent_code == "TOPIC_RAG"
    assert not result.needs_clarification


@pytest.mark.asyncio
async def test_classify_ambiguity_detection():
    """Top-1 和 Top-2 分数接近时应触发歧义澄清。"""
    llm_response = json.dumps([
        {"code": "TOPIC_RAG", "score": 0.65},
        {"code": "TOPIC_LLM", "score": 0.60},
        {"code": "TOPIC_EMB", "score": 0.1},
    ])
    llm = MockLLMService(response=llm_response)
    classifier = IntentClassifier(llm, high_threshold=0.8, low_threshold=0.5, gap_threshold=0.15)
    nodes = make_leaf_nodes()

    result = await classifier.classify("模型训练相关的问题", nodes)

    assert result.intent is not None
    assert result.needs_clarification is True
    # Top-1 score (0.65) - Top-2 score (0.60) = 0.05 < 0.15


@pytest.mark.asyncio
async def test_classify_low_confidence_global_search():
    """低置信度（<0.5）应进入全局搜索。"""
    llm_response = json.dumps([
        {"code": "TOPIC_RAG", "score": 0.3},
        {"code": "TOPIC_LLM", "score": 0.2},
        {"code": "TOPIC_EMB", "score": 0.1},
    ])
    llm = MockLLMService(response=llm_response)
    classifier = IntentClassifier(llm, low_threshold=0.5)
    nodes = make_leaf_nodes()

    result = await classifier.classify("今天天气怎么样", nodes)

    assert result.intent is None  # 全局搜索
    assert result.confidence == 0.3
    assert not result.needs_clarification


@pytest.mark.asyncio
async def test_classify_candidates_sorted():
    """候选列表应按分数降序排列。"""
    llm_response = json.dumps([
        {"code": "TOPIC_EMB", "score": 0.1},
        {"code": "TOPIC_RAG", "score": 0.9},
        {"code": "TOPIC_LLM", "score": 0.5},
    ])
    llm = MockLLMService(response=llm_response)
    classifier = IntentClassifier(llm)
    nodes = make_leaf_nodes()

    result = await classifier.classify("测试问题", nodes)

    scores = [score for _, score in result.candidates]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_classify_llm_malformed_response():
    """LLM 返回格式异常时应降级返回空结果。"""
    llm = MockLLMService(response="这不是JSON格式")
    classifier = IntentClassifier(llm)
    nodes = make_leaf_nodes()

    result = await classifier.classify("测试问题", nodes)

    assert result.intent is None
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_classify_only_non_leaf_nodes():
    """只有非叶节点时应返回空结果。"""
    llm = MockLLMService()
    classifier = IntentClassifier(llm)

    non_leaf = [
        IntentNode(intent_code="ROOT", name="根", level=0),
        IntentNode(intent_code="DOMAIN", name="领域", level=1, parent_code="ROOT"),
    ]

    result = await classifier.classify("测试问题", non_leaf)

    assert result.intent is None
    assert result.candidates == []


@pytest.mark.asyncio
async def test_classify_score_clamping():
    """分数应被限制在 [0, 1] 范围内。"""
    llm_response = json.dumps([
        {"code": "TOPIC_RAG", "score": 1.5},
        {"code": "TOPIC_LLM", "score": -0.3},
    ])
    llm = MockLLMService(response=llm_response)
    classifier = IntentClassifier(llm)
    nodes = make_leaf_nodes()

    result = await classifier.classify("测试问题", nodes)

    # 所有分数应在 [0, 1]
    for _, score in result.candidates:
        assert 0.0 <= score <= 1.0

    assert result.confidence == 1.0  # clamped from 1.5
