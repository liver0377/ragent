"""意图分类器 —— 基于大模型的用户意图识别。

核心职责：
    1. 接收用户问题和意图树，通过 LLM 对叶节点进行评分
    2. 根据阈值策略判定意图（直接命中 / 歧义 / 全局搜索）
    3. 返回 IntentResult 包含最优意图、置信度和候选列表
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from ragent.infra.ai.llm_service import LLMService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class IntentNode:
    """意图树节点。

    Attributes:
        intent_code:    意图编码，唯一标识。
        name:           意图名称（中文）。
        level:          层级（0=根, 1=领域, 2=主题/叶节点）。
        parent_code:    父节点编码，根节点为 ``None``。
        examples:       示例问题列表，用于辅助分类。
        collection_name:关联的向量检索集合名称（仅叶节点有效）。
        kind:           意图类型：0=RAG（知识库问答），1=TOOL（工具调用）。
        system_prompt:  自定义系统提示词。
        rag_prompt:     自定义 RAG 提示词。
    """

    intent_code: str
    name: str
    level: int
    parent_code: str | None = None
    examples: list[str] = field(default_factory=list)
    collection_name: str | None = None
    kind: int = 0
    system_prompt: str | None = None
    rag_prompt: str | None = None


@dataclass
class IntentResult:
    """意图分类结果。

    Attributes:
        intent:              最优意图节点，若无法确定则为 ``None``。
        confidence:          最优意图的置信度分数（0-1）。
        candidates:          所有候选意图及其分数，按分数降序排列。
        needs_clarification: 是否需要用户进一步澄清（存在歧义时为 ``True``）。
    """

    intent: IntentNode | None
    confidence: float
    candidates: list[tuple[IntentNode, float]]
    needs_clarification: bool


# ---------------------------------------------------------------------------
# 意图分类器
# ---------------------------------------------------------------------------


class IntentClassifier:
    """意图分类器 —— 利用 LLM 对用户问题进行意图识别。

    使用方式::

        from ragent.infra.ai.llm_service import LLMService

        classifier = IntentClassifier(llm_service)
        result = await classifier.classify("什么是RAG？", intent_tree=[...])
        print(result.intent.name, result.confidence)
    """

    def __init__(
        self,
        llm_service: LLMService,
        high_threshold: float = 0.8,
        low_threshold: float = 0.5,
        gap_threshold: float = 0.15,
    ) -> None:
        """初始化意图分类器。

        Args:
            llm_service:    LLM 服务实例。
            high_threshold: 高置信度阈值，高于此值直接命中。默认 0.8。
            low_threshold:  低置信度阈值，低于此值进入全局搜索。默认 0.5。
            gap_threshold:  歧义间隔阈值，Top-1 与 Top-2 差值小于此值时需澄清。默认 0.15。
        """
        self._llm = llm_service
        self._high_threshold = high_threshold
        self._low_threshold = low_threshold
        self._gap_threshold = gap_threshold

    async def classify(
        self,
        question: str,
        intent_tree: list[IntentNode],
    ) -> IntentResult:
        """对用户问题进行意图分类。

        处理步骤：
            1. 收集所有叶节点（level == 2）
            2. 将问题 + 叶节点名称列表发送给 LLM 进行评分
            3. 按分数降序排序
            4. 根据阈值策略判定意图

        Args:
            question:    用户问题。
        intent_tree: 意图树节点列表。

        Returns:
            IntentResult: 分类结果。
        """
        # 收集叶节点
        leaf_nodes = [node for node in intent_tree if node.level == 2]

        if not leaf_nodes:
            # 无意图树时直接返回全局搜索结果
            return IntentResult(
                intent=None,
                confidence=0.0,
                candidates=[],
                needs_clarification=False,
            )

        # LLM 评分
        scored = await self._score_with_llm(question, leaf_nodes)

        if not scored:
            return IntentResult(
                intent=None,
                confidence=0.0,
                candidates=[],
                needs_clarification=False,
            )

        # 按分数降序排序
        scored.sort(key=lambda x: x[1], reverse=True)

        top1_node, top1_score = scored[0]
        top2_score = scored[1][1] if len(scored) > 1 else 0.0

        # 阈值判定
        intent: IntentNode | None = None
        needs_clarification = False

        if top1_score >= self._high_threshold:
            # 高置信度：直接命中
            intent = top1_node
        elif top1_score >= self._low_threshold:
            # 中等置信度：检查歧义
            gap = top1_score - top2_score
            if gap < self._gap_threshold:
                # 歧义：需要用户澄清
                needs_clarification = True
                intent = top1_node
            else:
                # 无歧义：接受 Top-1
                intent = top1_node
        else:
            # 低置信度：全局搜索，不指定具体意图
            intent = None

        logger.debug(
            "意图分类: question='%s', intent=%s, confidence=%.2f, needs_clarification=%s",
            question,
            intent.name if intent else "全局搜索",
            top1_score,
            needs_clarification,
        )

        return IntentResult(
            intent=intent,
            confidence=top1_score,
            candidates=scored,
            needs_clarification=needs_clarification,
        )

    # ------------------------------------------------------------------ #
    # LLM 评分
    # ------------------------------------------------------------------ #

    async def _score_with_llm(
        self,
        question: str,
        leaf_nodes: list[IntentNode],
    ) -> list[tuple[IntentNode, float]]:
        """利用 LLM 对每个叶节点进行相关性评分。

        Args:
            question:   用户问题。
            leaf_nodes: 叶节点列表。

        Returns:
            list[tuple[IntentNode, float]]: (节点, 分数) 列表。
        """
        # 构造意图列表文本
        intent_lines: list[str] = []
        for i, node in enumerate(leaf_nodes):
            examples_str = "；".join(node.examples[:3]) if node.examples else "无示例"
            intent_lines.append(f"{i + 1}. {node.name}（编码: {node.intent_code}，示例: {examples_str}）")

        intent_text = "\n".join(intent_lines)

        prompt = (
            "你是一个意图分类评分专家。请对用户问题与以下意图的相关性进行评分。\n\n"
            "评分规则：\n"
            "1. 每个意图给出 0 到 1 之间的分数，表示相关性\n"
            "2. 1.0 表示完全匹配，0.0 表示完全不相关\n"
            "3. 评分时参考意图的示例问题\n\n"
            f"用户问题：{question}\n\n"
            f"意图列表：\n{intent_text}\n\n"
            "请以 JSON 格式输出评分结果，格式如下：\n"
            '[{"code": "意图编码", "score": 0.9}, ...]\n'
            "只输出 JSON 数组，不要其他内容。"
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.chat(messages)
            response = response.strip()

            # 提取 JSON 数组
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if not match:
                logger.warning("意图评分 LLM 返回格式异常: %s", response[:200])
                return []

            raw_scores: list[dict[str, Any]] = json.loads(match.group())

            # 构建编码到节点的映射
            code_to_node: dict[str, IntentNode] = {
                node.intent_code: node for node in leaf_nodes
            }

            scored: list[tuple[IntentNode, float]] = []
            for item in raw_scores:
                code = item.get("code", "")
                score = float(item.get("score", 0.0))
                score = max(0.0, min(1.0, score))  # 限制在 [0, 1]
                node = code_to_node.get(code)
                if node is not None:
                    scored.append((node, score))

            return scored

        except (json.JSONDecodeError, ValueError, Exception):
            logger.warning("意图评分失败", exc_info=True)
            return []
