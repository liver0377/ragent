"""测试会话记忆管理 —— SessionMemoryManager 单元测试。

覆盖场景：
    - 添加消息和获取记忆
    - 窗口大小限制
    - 摘要触发判断
    - 摘要生成
    - 空会话处理
    - 清除会话
"""

from __future__ import annotations

import pytest

from ragent.rag.memory.session_memory import (
    MemoryMessage,
    SessionMemory,
    SessionMemoryManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockLLMService:
    """模拟 LLM 服务。"""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or ["这是一个对话摘要。"]
        self._call_count = 0

    async def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
        else:
            resp = "默认摘要"
        self._call_count += 1
        return resp

    @property
    def call_count(self) -> int:
        return self._call_count


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_and_get_message():
    """添加消息后应能正确获取。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm)

    await manager.add_message(1, "user", "你好")
    await manager.add_message(1, "assistant", "你好！有什么可以帮助你的？")

    memory = await manager.get_memory(1)

    assert memory.conversation_id == 1
    assert len(memory.recent_messages) == 2
    assert memory.recent_messages[0].role == "user"
    assert memory.recent_messages[0].content == "你好"
    assert memory.recent_messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_get_empty_memory():
    """不存在的会话应返回空记忆。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm)

    memory = await manager.get_memory(999)

    assert memory.conversation_id == 999
    assert memory.summary is None
    assert memory.recent_messages == []


@pytest.mark.asyncio
async def test_window_size_limit():
    """消息数量超过窗口大小时应只返回窗口内的消息。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm, window_size=4)

    # 添加 6 条消息
    for i in range(6):
        await manager.add_message(1, "user" if i % 2 == 0 else "assistant", f"消息{i}")

    memory = await manager.get_memory(1)

    # 窗口大小为 4，应只返回最后 4 条
    assert len(memory.recent_messages) == 4
    assert memory.recent_messages[0].content == "消息2"
    assert memory.recent_messages[-1].content == "消息5"


@pytest.mark.asyncio
async def test_should_summarize_below_threshold():
    """消息数量未达阈值时不应触发摘要。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm, summarize_threshold=10)

    for i in range(5):
        await manager.add_message(1, "user", f"消息{i}")

    assert await manager.should_summarize(1) is False


@pytest.mark.asyncio
async def test_should_summarize_above_threshold():
    """消息数量达到阈值时应触发摘要。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm, summarize_threshold=5)

    for i in range(5):
        await manager.add_message(1, "user", f"消息{i}")

    assert await manager.should_summarize(1) is True


@pytest.mark.asyncio
async def test_summarize_generates_summary():
    """摘要生成应调用 LLM 并更新摘要。"""
    llm = MockLLMService(responses=["关于RAG技术的讨论摘要"])
    manager = SessionMemoryManager(llm, window_size=10, summarize_threshold=5)

    # 添加足够多的消息
    for i in range(5):
        await manager.add_message(1, "user" if i % 2 == 0 else "assistant", f"关于RAG的消息{i}")

    summary = await manager.summarize(1)

    assert summary == "关于RAG技术的讨论摘要"
    assert llm.call_count >= 1

    # 验证摘要已存储
    memory = await manager.get_memory(1)
    assert memory.summary == "关于RAG技术的讨论摘要"


@pytest.mark.asyncio
async def test_summarize_merges_existing():
    """已有摘要时应合并新旧摘要。"""
    llm = MockLLMService(responses=[
        "新摘要内容",       # 第一次摘要
        "合并后的摘要内容",  # 合并摘要
    ])
    manager = SessionMemoryManager(llm, window_size=4, summarize_threshold=2)

    # 第一轮对话
    await manager.add_message(1, "user", "问题1")
    await manager.add_message(1, "assistant", "回答1")
    await manager.summarize(1)

    assert llm.call_count == 1

    # 第二轮对话
    await manager.add_message(1, "user", "问题2")
    await manager.add_message(1, "assistant", "回答2")
    await manager.summarize(1)

    # 应该调用了合并（第二次调用）
    assert llm.call_count == 3  # 新摘要 + 合并 = 2 次额外调用


@pytest.mark.asyncio
async def test_summarize_empty_conversation():
    """空会话生成摘要应返回空字符串。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm)

    summary = await manager.summarize(1)

    assert summary == ""
    assert llm.call_count == 0


@pytest.mark.asyncio
async def test_get_message_count():
    """消息计数应正确。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm)

    assert manager.get_message_count(1) == 0

    await manager.add_message(1, "user", "你好")
    await manager.add_message(1, "assistant", "你好！")

    assert manager.get_message_count(1) == 2


@pytest.mark.asyncio
async def test_clear_session():
    """清除会话后应无法获取消息。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm)

    await manager.add_message(1, "user", "你好")
    manager.clear_session(1)

    memory = await manager.get_memory(1)
    assert memory.recent_messages == []
    assert memory.summary is None


@pytest.mark.asyncio
async def test_multiple_sessions():
    """多个会话应独立管理。"""
    llm = MockLLMService()
    manager = SessionMemoryManager(llm)

    await manager.add_message(1, "user", "会话1的消息")
    await manager.add_message(2, "user", "会话2的消息")

    memory1 = await manager.get_memory(1)
    memory2 = await manager.get_memory(2)

    assert len(memory1.recent_messages) == 1
    assert memory1.recent_messages[0].content == "会话1的消息"
    assert len(memory2.recent_messages) == 1
    assert memory2.recent_messages[0].content == "会话2的消息"
