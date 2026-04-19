"""测试 Prompt 组装器 —— PromptBuilder 单元测试。

覆盖场景：
    - 基本消息组装
    - 包含上下文的组装
    - 包含历史的组装
    - 自定义系统提示词
    - 自定义 RAG 提示词
    - 完整组装（上下文 + 历史 + 当前消息）
    - 上下文格式化
"""

from __future__ import annotations

import pytest

from ragent.rag.prompt.prompt_builder import PromptBuilder, DEFAULT_SYSTEM_PROMPT, DEFAULT_RAG_PROMPT


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_basic():
    """基本组装：只有当前消息。"""
    builder = PromptBuilder()

    messages = await builder.build(
        messages=[{"role": "user", "content": "你好"}],
    )

    assert len(messages) == 2  # system + user
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == DEFAULT_SYSTEM_PROMPT
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "你好"


@pytest.mark.asyncio
async def test_build_with_context():
    """包含检索上下文时应在系统提示词后添加 RAG 提示。"""
    builder = PromptBuilder()

    messages = await builder.build(
        messages=[{"role": "user", "content": "什么是RAG？"}],
        context="RAG是检索增强生成的缩写。",
    )

    assert len(messages) == 3  # system + rag_context + user
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "system"
    assert "[1] RAG是检索增强生成的缩写。" in messages[1]["content"]
    assert messages[2]["role"] == "user"


@pytest.mark.asyncio
async def test_build_with_history():
    """包含对话历史时应插入历史消息。"""
    builder = PromptBuilder()

    history = [
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的回答"},
    ]

    messages = await builder.build(
        messages=[{"role": "user", "content": "新的问题"}],
        history=history,
    )

    # system + history_user + history_assistant + current_user
    assert len(messages) == 4
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "之前的问题"
    assert messages[2]["role"] == "assistant"
    assert messages[2]["content"] == "之前的回答"
    assert messages[3]["role"] == "user"
    assert messages[3]["content"] == "新的问题"


@pytest.mark.asyncio
async def test_build_with_context_and_history():
    """完整组装：上下文 + 历史 + 当前消息。"""
    builder = PromptBuilder()

    history = [
        {"role": "user", "content": "什么是AI？"},
        {"role": "assistant", "content": "AI是人工智能的缩写。"},
    ]

    messages = await builder.build(
        messages=[{"role": "user", "content": "AI的应用有哪些？"}],
        context="AI在医疗、金融等领域有广泛应用。",
        history=history,
    )

    # system + rag_context + history_user + history_assistant + current_user
    assert len(messages) == 5
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "system"
    assert messages[2]["role"] == "user"
    assert messages[3]["role"] == "assistant"
    assert messages[4]["role"] == "user"


@pytest.mark.asyncio
async def test_build_custom_system_prompt():
    """自定义系统提示词应覆盖默认值。"""
    builder = PromptBuilder()
    custom_prompt = "你是一个专业的医疗助手。"

    messages = await builder.build(
        messages=[{"role": "user", "content": "你好"}],
        system_prompt=custom_prompt,
    )

    assert messages[0]["content"] == custom_prompt


@pytest.mark.asyncio
async def test_build_custom_rag_prompt():
    """自定义 RAG 提示词应覆盖默认值。"""
    builder = PromptBuilder()
    custom_rag = "参考资料：\n{context}\n\n请回答问题："

    messages = await builder.build(
        messages=[{"role": "user", "content": "测试"}],
        context="参考内容",
        rag_prompt=custom_rag,
    )

    assert "参考资料：" in messages[1]["content"]
    assert "参考内容" in messages[1]["content"]


@pytest.mark.asyncio
async def test_build_no_context():
    """无上下文时不应添加 RAG 提示消息。"""
    builder = PromptBuilder()

    messages = await builder.build(
        messages=[{"role": "user", "content": "你好"}],
        context=None,
    )

    # 只有 system + user
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_build_empty_history_filtered():
    """历史中空内容的消息应被过滤。"""
    builder = PromptBuilder()

    history = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "有效回答"},
        {"role": "system", "content": "系统消息"},
    ]

    messages = await builder.build(
        messages=[{"role": "user", "content": "问题"}],
        history=history,
    )

    # system + assistant(history) + user(current)
    # 空 user 消息和 system 角色消息应被过滤
    assert len(messages) == 3
    assert messages[1]["content"] == "有效回答"


def test_format_context_single_chunk():
    """单块上下文应格式化为 [1] 文本。"""
    builder = PromptBuilder()
    formatted = builder._format_context("单块文本内容")

    assert formatted == "[1] 单块文本内容"


def test_format_context_multiple_chunks():
    """多块上下文应格式化为编号列表。"""
    builder = PromptBuilder()
    formatted = builder._format_context("第一块\n第二块\n第三块")

    assert "[1] 第一块" in formatted
    assert "[2] 第二块" in formatted
    assert "[3] 第三块" in formatted


def test_format_context_already_numbered():
    """已编号的上下文应保持原样。"""
    builder = PromptBuilder()
    original = "[1] 已有编号的内容\n[2] 第二条"
    formatted = builder._format_context(original)

    assert formatted == original


def test_format_context_empty():
    """空上下文应返回原始输入。"""
    builder = PromptBuilder()
    assert builder._format_context("") == ""
    # 纯空白内容：strip 后无有效块，返回原始
    assert builder._format_context("  \n  ") == "  \n  "


@pytest.mark.asyncio
async def test_build_preserves_message_order():
    """消息顺序应正确：system → context → history → current。"""
    builder = PromptBuilder()

    messages = await builder.build(
        messages=[{"role": "user", "content": "当前问题"}],
        context="上下文内容",
        history=[
            {"role": "user", "content": "历史问题"},
            {"role": "assistant", "content": "历史回答"},
        ],
    )

    roles = [m["role"] for m in messages]
    assert roles == ["system", "system", "user", "assistant", "user"]

    assert messages[-1]["content"] == "当前问题"
