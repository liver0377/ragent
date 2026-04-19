"""问题重写器 —— 查询理解、上下文补全、同义词归一化、复杂问题拆分。

核心职责：
    1. 上下文补全 —— 基于对话历史，利用 LLM 解析代词/省略，补全为独立可理解的问题
    2. 关键词归一化 —— 查询同义词映射表，将同义词映射为标准术语
    3. 复杂问题拆分 —— 利用 LLM 将多部分问题拆分为子问题列表
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ragent.infra.ai.llm_service import LLMService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class RewriteResult:
    """问题重写结果。

    Attributes:
        rewritten:        重写后的问题文本。
        sub_questions:    拆分后的子问题列表（若无需拆分则为空列表）。
        normalized_terms: 同义词归一化映射，键为原始词，值为标准术语。
    """

    rewritten: str
    sub_questions: list[str] = field(default_factory=list)
    normalized_terms: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 默认同义词映射表（模拟 t_query_term_mapping 表）
# ---------------------------------------------------------------------------

_DEFAULT_TERM_MAPPING: dict[str, str] = {
    # 示例：同义词 → 标准术语
    "AI": "人工智能",
    "ai": "人工智能",
    "ML": "机器学习",
    "ml": "机器学习",
    "DL": "深度学习",
    "dl": "深度学习",
    "NLP": "自然语言处理",
    "nlp": "自然语言处理",
    "LLM": "大语言模型",
    "llm": "大语言模型",
    "RAG": "检索增强生成",
    "rag": "检索增强生成",
    "GPT": "大语言模型",
    "知识图谱": "知识图谱",
    "KG": "知识图谱",
}


# ---------------------------------------------------------------------------
# 问题重写器
# ---------------------------------------------------------------------------


class QueryRewriter:
    """问题重写器 —— 对用户原始问题进行理解、补全和拆分。

    使用方式::

        from ragent.infra.ai.llm_service import LLMService

        rewriter = QueryRewriter(llm_service)
        result = await rewriter.rewrite("它是什么？", history=[...])
        print(result.rewritten)       # 补全后的问题
        print(result.sub_questions)   # 拆分的子问题
    """

    def __init__(
        self,
        llm_service: LLMService,
        term_mapping: dict[str, str] | None = None,
        enable_split: bool = True,
    ) -> None:
        """初始化问题重写器。

        Args:
            llm_service:  LLM 服务实例，用于上下文补全和问题拆分。
            term_mapping: 同义词映射表，若为 ``None`` 则使用默认映射。
            enable_split: 是否启用复杂问题拆分，默认 ``True``。
        """
        self._llm = llm_service
        self._term_mapping = term_mapping or _DEFAULT_TERM_MAPPING
        self._enable_split = enable_split

    async def rewrite(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> RewriteResult:
        """重写用户问题。

        处理步骤：
            1. 上下文补全 —— 基于对话历史解析代词/省略
            2. 关键词归一化 —— 将同义词映射为标准术语
            3. 复杂问题拆分 —— 将多部分问题拆分为子问题

        Args:
            question: 用户原始问题。
            history:  对话历史，格式为 ``[{"role": "user"/"assistant", "content": "..."}]``。

        Returns:
            RewriteResult: 重写结果，包含重写后的问题、子问题和归一化映射。
        """
        # 第一步：上下文补全
        completed = await self._context_completion(question, history)

        # 第二步：关键词归一化
        normalized, term_map = self._normalize_terms(completed)

        # 第三步：复杂问题拆分
        sub_questions: list[str] = []
        if self._enable_split:
            sub_questions = await self._split_complex(normalized)

        return RewriteResult(
            rewritten=normalized,
            sub_questions=sub_questions,
            normalized_terms=term_map,
        )

    # ------------------------------------------------------------------ #
    # 上下文补全
    # ------------------------------------------------------------------ #

    async def _context_completion(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """利用 LLM 基于对话历史补全问题中的代词和省略部分。

        若无对话历史，则直接返回原始问题。

        Args:
            question: 用户原始问题。
            history:  对话历史。

        Returns:
            str: 补全后的问题。
        """
        if not history:
            return question

        # 构造对话历史摘要
        history_text = "\n".join(
            f"{'用户' if msg.get('role') == 'user' else '助手'}: {msg.get('content', '')}"
            for msg in history[-6:]  # 最近 3 轮
        )

        prompt = (
            '你是一个问题补全助手。根据对话历史，将用户最新问题中的代词（如"它"、"这个"、"那个"）'
            "和省略部分替换为具体的名词或短语，使其成为一个独立可理解的问题。\n\n"
            "规则：\n"
            "1. 只输出补全后的问题，不要解释\n"
            "2. 如果问题已经独立可理解，直接原样返回\n"
            "3. 不要添加问题中没有的信息\n\n"
            f"对话历史：\n{history_text}\n\n"
            f"用户最新问题：{question}\n\n"
            "补全后的问题："
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            result = await self._llm.chat(messages)
            completed = result.strip()
            if completed:
                logger.debug("上下文补全: '%s' -> '%s'", question, completed)
                return completed
        except Exception:
            logger.warning("上下文补全失败，使用原始问题", exc_info=True)

        return question

    # ------------------------------------------------------------------ #
    # 关键词归一化
    # ------------------------------------------------------------------ #

    def _normalize_terms(self, text: str) -> tuple[str, dict[str, str]]:
        """将文本中的同义词替换为标准术语。

        Args:
            text: 待归一化的文本。

        Returns:
            tuple[str, dict[str, str]]: (归一化后的文本, 映射字典)。
        """
        normalized_terms: dict[str, str] = {}
        result = text

        for synonym, standard in self._term_mapping.items():
            # 使用正则进行全词匹配替换
            pattern = re.compile(re.escape(synonym), re.IGNORECASE)
            if pattern.search(result):
                normalized_terms[synonym] = standard
                result = pattern.sub(standard, result)

        if normalized_terms:
            logger.debug("关键词归一化: %s", normalized_terms)

        return result, normalized_terms

    # ------------------------------------------------------------------ #
    # 复杂问题拆分
    # ------------------------------------------------------------------ #

    async def _split_complex(self, question: str) -> list[str]:
        """利用 LLM 将复杂多部分问题拆分为子问题。

        判断依据：如果问题包含多个独立的子意图，则拆分。

        Args:
            question: 待拆分的问题。

        Returns:
            list[str]: 子问题列表。若问题简单则返回空列表。
        """
        prompt = (
            "判断以下问题是否包含多个独立的子问题。如果是，请拆分为多个子问题；"
            "如果不是，返回空列表。\n\n"
            "规则：\n"
            "1. 以 JSON 数组格式输出，例如 [\"子问题1\", \"子问题2\"]\n"
            "2. 如果只有一个问题，输出空数组 []\n"
            "3. 只输出 JSON 数组，不要其他内容\n\n"
            f"问题：{question}\n\n"
            "输出："
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.chat(messages)
            response = response.strip()

            # 尝试提取 JSON 数组
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if match:
                sub_questions = json.loads(match.group())
                if isinstance(sub_questions, list) and len(sub_questions) >= 2:
                    # 验证每个元素都是字符串
                    valid = [q for q in sub_questions if isinstance(q, str) and q.strip()]
                    if len(valid) >= 2:
                        logger.debug("复杂问题拆分: '%s' -> %s", question, valid)
                        return valid

        except (json.JSONDecodeError, Exception):
            logger.warning("复杂问题拆分失败", exc_info=True)

        return []
