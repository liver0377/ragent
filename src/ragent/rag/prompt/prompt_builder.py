"""Prompt 组装器 —— 构建完整的 LLM 对话消息列表。

核心职责：
    1. 组装系统提示词（角色定义 + 行为规则 + 领域约束）
    2. 格式化检索上下文为编号引用
    3. 插入对话历史（摘要 + 最近消息）
    4. 拼接当前问题

模板结构：
    [System Prompt]
    [Context References]
    [History Summary]
    [Recent Messages]
    [Current Question]
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 默认系统提示词
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """你是一个专业的智能问答助手。你的职责是基于提供的参考资料，准确、专业地回答用户的问题。

## 行为规则
1. 严格基于提供的参考资料回答问题，不要编造信息
2. 如果参考资料不足以回答问题，请如实告知用户
3. 回答要条理清晰、结构化，适当使用列表和标题
4. 使用专业但易懂的语言
5. 如果涉及多个方面，请逐一说明

## 输出要求
- 使用中文回答
- 回答要有逻辑性和完整性
- 适当引用参考资料的编号（如 [1]、[2]）"""

DEFAULT_RAG_PROMPT = """请基于以下参考资料回答用户的问题。

参考资料：
{context}

要求：
1. 优先使用参考资料中的信息
2. 如实反映参考资料的内容，不要编造
3. 如果参考资料中没有相关信息，请说明"""


# ---------------------------------------------------------------------------
# Prompt 组装器
# ---------------------------------------------------------------------------


class PromptBuilder:
    """Prompt 组装器 —— 构建 LLM 对话所需的完整消息列表。

    使用方式::

        builder = PromptBuilder()
        messages = await builder.build(
            messages=[{"role": "user", "content": "什么是RAG？"}],
            context="检索到的参考资料...",
            history=[{"role": "user", "content": "之前的问题"}],
        )
    """

    def __init__(
        self,
        default_system_prompt: str | None = None,
        default_rag_prompt: str | None = None,
    ) -> None:
        """初始化 Prompt 组装器。

        Args:
            default_system_prompt: 默认系统提示词，若为 ``None`` 则使用内置默认。
            default_rag_prompt:    默认 RAG 提示词模板，若为 ``None`` 则使用内置默认。
        """
        self._system_prompt = default_system_prompt or DEFAULT_SYSTEM_PROMPT
        self._rag_prompt = default_rag_prompt or DEFAULT_RAG_PROMPT

    async def build(
        self,
        messages: list[dict[str, str]],
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        rag_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """组装完整的 LLM 对话消息列表。

        消息列表结构：
            1. System prompt（角色定义 + 行为规则）
            2. RAG 上下文（如有检索结果，格式化为编号引用）
            3. 历史消息（如有）
            4. 当前问题

        Args:
            messages:      当前消息列表（至少包含一条用户消息）。
            context:       检索到的上下文文本。
            history:       对话历史，格式为 ``[{"role": "...", "content": "..."}]``。
            system_prompt: 自定义系统提示词，覆盖默认值。
            rag_prompt:    自定义 RAG 提示词模板，覆盖默认值。

        Returns:
            list[dict[str, str]]: 组装完成的消息列表，可直接传给 LLM。
        """
        result: list[dict[str, str]] = []

        # 步骤 1：系统提示词
        sys_prompt = system_prompt or self._system_prompt
        result.append({"role": "system", "content": sys_prompt})

        # 步骤 2：RAG 上下文
        if context:
            formatted_context = self._format_context(context)
            rag_tpl = rag_prompt or self._rag_prompt
            rag_content = rag_tpl.replace("{context}", formatted_context)
            result.append({"role": "system", "content": rag_content})

        # 步骤 3：对话历史
        if history:
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    result.append({"role": role, "content": content})

        # 步骤 4：当前消息
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                result.append({"role": role, "content": content})

        logger.debug("Prompt 组装: 共 %d 条消息, 上下文=%s", len(result), "有" if context else "无")
        return result

    # ------------------------------------------------------------------ #
    # 上下文格式化
    # ------------------------------------------------------------------ #

    def _format_context(self, context: str) -> str:
        """将上下文文本格式化为编号引用格式。

        输入: 以换行分隔的文本块
        输出: [1] 第一块\n[2] 第二块\n...

        如果输入已经是编号格式或为单块文本，直接返回。

        Args:
            context: 原始上下文文本。

        Returns:
            str: 格式化后的编号引用文本。
        """
        # 如果已经包含编号格式，直接返回
        if context.strip().startswith("[1]"):
            return context

        # 按段落分割
        chunks = [chunk.strip() for chunk in context.split("\n") if chunk.strip()]

        if not chunks:
            return context

        if len(chunks) == 1:
            return f"[1] {chunks[0]}"

        # 编号格式化
        formatted = "\n".join(f"[{i + 1}] {chunk}" for i, chunk in enumerate(chunks))
        return formatted
