"""会话记忆管理 —— 对话历史存储、窗口管理与摘要生成。

核心职责：
    1. 管理会话消息的存取（基于内存字典，后续接入数据库）
    2. 滑动窗口控制：超过窗口大小时触发摘要
    3. 利用 LLM 生成对话摘要，压缩历史信息

设计要点：
    - 使用可配置的窗口大小（默认 10 轮 = 20 条消息）
    - 当前使用内存字典存储，后续通过 Repository 模式接入数据库
    - 摘要生成由 LLM 完成，保留关键信息
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ragent.infra.ai.llm_service import LLMService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class MemoryMessage:
    """会话消息记录。

    Attributes:
        role:       消息角色（"user" / "assistant" / "system"）。
        content:    消息内容。
        created_at: 创建时间。
    """

    role: str
    content: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class SessionMemory:
    """会话记忆快照。

    Attributes:
        conversation_id: 会话 ID。
        summary:         对话摘要，无摘要时为 ``None``。
        recent_messages: 最近的对话消息列表。
    """

    conversation_id: int
    summary: str | None = None
    recent_messages: list[MemoryMessage] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 会话记忆管理器
# ---------------------------------------------------------------------------


class SessionMemoryManager:
    """会话记忆管理器 —— 管理对话历史的存取和摘要。

    使用方式::

        from ragent.infra.ai.llm_service import LLMService

        manager = SessionMemoryManager(llm_service)
        await manager.add_message(1, "user", "什么是RAG？")
        memory = await manager.get_memory(1)
    """

    def __init__(
        self,
        llm_service: LLMService,
        window_size: int = 20,
        summarize_threshold: int = 20,
    ) -> None:
        """初始化会话记忆管理器。

        Args:
            llm_service:         LLM 服务实例，用于生成摘要。
            window_size:         滑动窗口大小（消息条数），默认 20（10 轮对话）。
            summarize_threshold: 触发摘要的消息阈值，默认与 window_size 相同。
        """
        self._llm = llm_service
        self._window_size = window_size
        self._summarize_threshold = summarize_threshold

        # 内存存储：conversation_id -> (summary, [MemoryMessage])
        self._store: dict[int, tuple[str | None, list[MemoryMessage]]] = {}

    async def get_memory(self, conversation_id: int) -> SessionMemory:
        """获取会话记忆。

        返回指定会话的摘要和最近窗口内的消息。

        Args:
            conversation_id: 会话 ID。

        Returns:
            SessionMemory: 会话记忆快照。
        """
        summary, messages = self._store.get(conversation_id, (None, []))

        # 只返回窗口内的最近消息
        recent = messages[-self._window_size :] if messages else []

        return SessionMemory(
            conversation_id=conversation_id,
            summary=summary,
            recent_messages=list(recent),
        )

    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
    ) -> None:
        """向会话中添加一条消息。

        Args:
            conversation_id: 会话 ID。
            role:            消息角色。
            content:         消息内容。
        """
        summary, messages = self._store.get(conversation_id, (None, []))

        msg = MemoryMessage(role=role, content=content)
        messages.append(msg)

        self._store[conversation_id] = (summary, messages)

        logger.debug(
            "会话记忆: 添加消息 conv_id=%d, role=%s, 总消息数=%d",
            conversation_id,
            role,
            len(messages),
        )

    async def should_summarize(self, conversation_id: int) -> bool:
        """检查会话是否需要生成摘要。

        当消息数量超过阈值时返回 ``True``。

        Args:
            conversation_id: 会话 ID。

        Returns:
            bool: 是否需要生成摘要。
        """
        _, messages = self._store.get(conversation_id, (None, []))
        return len(messages) >= self._summarize_threshold

    async def summarize(self, conversation_id: int) -> str:
        """利用 LLM 生成会话摘要。

        将当前所有对话内容发送给 LLM 进行摘要，生成后：
            1. 更新存储中的摘要
            2. 保留最近窗口内的消息，其余清除

        Args:
            conversation_id: 会话 ID。

        Returns:
            str: 生成的摘要文本。
        """
        summary, messages = self._store.get(conversation_id, (None, []))

        if not messages:
            return summary or ""

        # 构造对话文本
        conversation_text = "\n".join(
            f"{'用户' if msg.role == 'user' else '助手'}: {msg.content}"
            for msg in messages
        )

        prompt = (
            "请对以下对话内容生成一个简洁的摘要，保留关键信息和重要细节。\n"
            "摘要应该能帮助理解对话的主题和进展。\n\n"
            f"对话内容：\n{conversation_text}\n\n"
            "摘要："
        )

        try:
            messages_for_llm = [{"role": "user", "content": prompt}]
            new_summary = await self._llm.chat(messages_for_llm)
            new_summary = new_summary.strip()

            # 如果已有旧摘要，合并
            if summary:
                merge_prompt = (
                    "请将以下两段对话摘要合并为一段完整的摘要，保留所有重要信息：\n\n"
                    f"旧摘要：{summary}\n\n"
                    f"新内容摘要：{new_summary}\n\n"
                    "合并后的摘要："
                )
                merge_messages = [{"role": "user", "content": merge_prompt}]
                new_summary = await self._llm.chat(merge_messages)
                new_summary = new_summary.strip()

            # 更新存储：保留最近窗口内的消息
            recent = messages[-self._window_size :] if len(messages) > self._window_size else messages
            self._store[conversation_id] = (new_summary, recent)

            logger.info(
                "会话摘要生成: conv_id=%d, 摘要长度=%d, 保留消息=%d",
                conversation_id,
                len(new_summary),
                len(recent),
            )

            return new_summary

        except Exception:
            logger.error("会话摘要生成失败: conv_id=%d", conversation_id, exc_info=True)
            return summary or ""

    # ------------------------------------------------------------------ #
    # 辅助方法
    # ------------------------------------------------------------------ #

    def get_message_count(self, conversation_id: int) -> int:
        """获取会话的消息数量。

        Args:
            conversation_id: 会话 ID。

        Returns:
            int: 消息数量。
        """
        _, messages = self._store.get(conversation_id, (None, []))
        return len(messages)

    def clear_session(self, conversation_id: int) -> None:
        """清除指定会话的所有数据。

        Args:
            conversation_id: 会话 ID。
        """
        self._store.pop(conversation_id, None)
        logger.debug("会话记忆: 清除 conv_id=%d", conversation_id)
