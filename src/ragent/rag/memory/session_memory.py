"""会话记忆管理 —— 对话历史持久化到 PostgreSQL。

核心职责：
    1. 管理会话消息的存取（基于 PostgreSQL）
    2. 滑动窗口控制：超过窗口大小时触发摘要
    3. 利用 LLM 生成对话摘要，压缩历史信息

设计要点：
    - 消息和摘要均持久化到数据库（t_message / t_conversation_summary）
    - 使用可配置的窗口大小（默认 10 轮 = 20 条消息）
    - 摘要生成由 LLM 完成，保留关键信息
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ragent.common.models import Conversation, ConversationSummary, Message
from ragent.common.snowflake import generate_id
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
# 会话记忆管理器（数据库持久化版本）
# ---------------------------------------------------------------------------


class SessionMemoryManager:
    """会话记忆管理器 —— 管理对话历史的存取和摘要（数据库持久化）。

    使用方式::

        from ragent.infra.ai.llm_service import LLMService

        manager = SessionMemoryManager(llm_service, db_session)
        await manager.add_message(1, "user", "什么是RAG？")
        memory = await manager.get_memory(1)
    """

    def __init__(
        self,
        llm_service: LLMService,
        db_session: AsyncSession | None = None,
        window_size: int = 20,
        summarize_threshold: int = 20,
    ) -> None:
        """初始化会话记忆管理器。

        Args:
            llm_service:         LLM 服务实例，用于生成摘要。
            db_session:          异步数据库会话（可选，调用 set_db 后也可用）。
            window_size:         滑动窗口大小（消息条数），默认 20（10 轮对话）。
            summarize_threshold: 触发摘要的消息阈值，默认与 window_size 相同。
        """
        self._llm = llm_service
        self._db: AsyncSession | None = db_session
        self._window_size = window_size
        self._summarize_threshold = summarize_threshold

    def set_db(self, db: AsyncSession) -> None:
        """设置数据库会话（用于延迟注入）。"""
        self._db = db

    def _ensure_db(self) -> AsyncSession:
        if self._db is None:
            raise RuntimeError("SessionMemoryManager: 未设置数据库会话，请先调用 set_db()")
        return self._db

    async def get_memory(self, conversation_id: int) -> SessionMemory:
        """获取会话记忆。

        从数据库加载摘要和最近窗口内的消息。

        Args:
            conversation_id: 会话 ID。

        Returns:
            SessionMemory: 会话记忆快照。
        """
        db = self._ensure_db()

        # 获取最新摘要
        summary_result = await db.execute(
            select(ConversationSummary.content)
            .where(ConversationSummary.conversation_id == conversation_id)
            .order_by(ConversationSummary.created_at.desc())
            .limit(1)
        )
        summary_row = summary_result.scalar_one_or_none()
        summary = summary_row if summary_row else None

        # 获取最近窗口内的消息
        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(self._window_size)
        )
        messages = list(reversed(msg_result.scalars().all()))  # 按时间正序

        recent = [
            MemoryMessage(role=m.role, content=m.content, created_at=m.created_at)
            for m in messages
        ]

        return SessionMemory(
            conversation_id=conversation_id,
            summary=summary,
            recent_messages=recent,
        )

    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        user_id: int | None = None,
    ) -> None:
        """向会话中添加一条消息并持久化到数据库。

        Args:
            conversation_id: 会话 ID。
            role:            消息角色。
            content:         消息内容。
            user_id:         用户 ID（写入 t_message.user_id）。
        """
        db = self._ensure_db()

        msg = Message(
            id=generate_id(),
            conversation_id=conversation_id,
            user_id=user_id or 0,
            role=role,
            content=content,
        )
        db.add(msg)

        # 更新会话的最后消息时间
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_message_time=datetime.now())
        )

        await db.flush()

        logger.debug(
            "会话记忆: 添加消息 conv_id=%d, role=%s",
            conversation_id,
            role,
        )

    async def should_summarize(self, conversation_id: int) -> bool:
        """检查会话是否需要生成摘要。

        Args:
            conversation_id: 会话 ID。

        Returns:
            bool: 是否需要生成摘要。
        """
        db = self._ensure_db()

        result = await db.execute(
            select(func.count()).select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        count = result.scalar_one()
        return count >= self._summarize_threshold

    async def summarize(self, conversation_id: int) -> str:
        """利用 LLM 生成会话摘要并持久化到数据库。

        Args:
            conversation_id: 会话 ID。

        Returns:
            str: 生成的摘要文本。
        """
        db = self._ensure_db()

        # 获取所有消息用于摘要
        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        messages = list(msg_result.scalars().all())

        if not messages:
            return ""

        # 获取已有摘要
        summary_result = await db.execute(
            select(ConversationSummary.content)
            .where(ConversationSummary.conversation_id == conversation_id)
            .order_by(ConversationSummary.created_at.desc())
            .limit(1)
        )
        old_summary = summary_result.scalar_one_or_none()

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
            if old_summary:
                merge_prompt = (
                    "请将以下两段对话摘要合并为一段完整的摘要，保留所有重要信息：\n\n"
                    f"旧摘要：{old_summary}\n\n"
                    f"新内容摘要：{new_summary}\n\n"
                    "合并后的摘要："
                )
                merge_messages = [{"role": "user", "content": merge_prompt}]
                new_summary = await self._llm.chat(merge_messages)
                new_summary = new_summary.strip()

            # 写入摘要表
            summary_record = ConversationSummary(
                id=generate_id(),
                conversation_id=conversation_id,
                user_id=messages[0].user_id if messages else 0,
                content=new_summary,
                last_message_id=messages[-1].id if messages else None,
            )
            db.add(summary_record)
            await db.flush()

            logger.info(
                "会话摘要生成: conv_id=%d, 摘要长度=%d",
                conversation_id,
                len(new_summary),
            )

            return new_summary

        except Exception:
            logger.error("会话摘要生成失败: conv_id=%d", conversation_id, exc_info=True)
            return old_summary or ""

    # ------------------------------------------------------------------ #
    # 辅助方法
    # ------------------------------------------------------------------ #

    async def get_message_count(self, conversation_id: int) -> int:
        """获取会话的消息数量。"""
        db = self._ensure_db()
        result = await db.execute(
            select(func.count()).select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        return result.scalar_one()

    async def clear_session(self, conversation_id: int) -> None:
        """清除指定会话的所有数据。"""
        db = self._ensure_db()

        # 删除消息
        msgs = await db.execute(
            select(Message).where(Message.conversation_id == conversation_id)
        )
        for msg in msgs.scalars().all():
            await db.delete(msg)

        # 删除摘要
        sums = await db.execute(
            select(ConversationSummary).where(ConversationSummary.conversation_id == conversation_id)
        )
        for s in sums.scalars().all():
            await db.delete(s)

        await db.flush()
        logger.debug("会话记忆: 清除 conv_id=%d", conversation_id)
