"""
文档摄取管线节点模块 —— 定义管线中各处理阶段的节点实现。

核心职责：
    1. 提供抽象基类 :class:`IngestionNode`
    2. 实现六个具体节点类型：
       - :class:`FetcherNode`  —— 从文件系统读取文件
       - :class:`ParserNode`   —— 将文档解析为纯文本
       - :class:`EnhancerNode` —— 基于 LLM 的文本增强
       - :class:`ChunkerNode`  —— 文本分块（固定大小 / 结构感知）
       - :class:`EnricherNode` —— 分块级别的内容增强
       - :class:`IndexerNode`  —— 写入向量存储
    3. 提供 :data:`NODE_REGISTRY` 注册表，按类型名查找节点类

所有节点均实现 ``execute(ctx, settings)`` 和 ``should_execute(ctx, condition)`` 接口。
"""

from __future__ import annotations

import abc
import hashlib
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, ClassVar

from ragent.ingestion.context import ChunkData, IngestionContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 文件扩展名 → 类型映射
# ---------------------------------------------------------------------------

_EXTENSION_MAP: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".text": "txt",
    ".csv": "txt",
}


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class IngestionNode(abc.ABC):
    """摄取管线节点抽象基类。

    所有节点必须实现 :meth:`execute` 方法，处理上下文中的数据。
    可选重写 :meth:`should_execute` 以支持条件跳过逻辑。

    Attributes:
        node_type: 节点类型标识字符串。
    """

    @property
    @abc.abstractmethod
    def node_type(self) -> str:
        """返回节点类型标识字符串。"""
        ...

    @abc.abstractmethod
    async def execute(
        self,
        ctx: IngestionContext,
        settings: dict[str, Any] | None = None,
    ) -> None:
        """执行节点逻辑，修改传入的上下文对象。

        Args:
            ctx:      管线执行上下文。
            settings: 节点配置参数（可选）。
        """
        ...

    async def should_execute(
        self,
        ctx: IngestionContext,
        condition: dict[str, Any] | None = None,
    ) -> bool:
        """判断当前节点是否应该执行。

        默认实现始终返回 ``True``。子类可重写以支持条件跳过。

        Args:
            ctx:       管线执行上下文。
            condition: 条件配置（可选），例如 ``{"file_type": "pdf"}``。

        Returns:
            是否执行当前节点。
        """
        if condition is None:
            return True

        # 支持 file_type 条件过滤
        if "file_type" in condition:
            expected = condition["file_type"]
            if isinstance(expected, str):
                expected = [expected]
            if ctx.file_type not in expected:
                logger.debug(
                    "节点 %s 跳过: file_type=%s 不在 %s 中",
                    self.node_type,
                    ctx.file_type,
                    expected,
                )
                return False

        # 支持 source_type 条件过滤
        if "source_type" in condition:
            expected = condition["source_type"]
            if isinstance(expected, str):
                expected = [expected]
            if ctx.source_type not in expected:
                logger.debug(
                    "节点 %s 跳过: source_type=%s 不在 %s 中",
                    self.node_type,
                    ctx.source_type,
                    expected,
                )
                return False

        return True


# ---------------------------------------------------------------------------
# FetcherNode —— 文件读取节点
# ---------------------------------------------------------------------------

class FetcherNode(IngestionNode):
    """文件读取节点 —— 从本地文件系统读取文件内容。

    支持 ``local`` 来源类型。读取文件字节并写入 ``ctx.raw_bytes``，
    同时根据文件扩展名检测 ``ctx.file_type``。

    Settings:
        source_type: 来源类型，默认 ``"local"``。
    """

    @property
    def node_type(self) -> str:
        """返回节点类型标识。"""
        return "fetcher"

    async def execute(
        self,
        ctx: IngestionContext,
        settings: dict[str, Any] | None = None,
    ) -> None:
        """从文件系统读取文件内容到上下文。

        Args:
            ctx:      管线执行上下文。
            settings: 节点配置（可选）。

        Raises:
            FileNotFoundError: 文件不存在。
            IOError:           文件读取失败。
        """
        source_type = (settings or {}).get("source_type", ctx.source_type)

        if source_type == "local":
            path = Path(ctx.source_location)
            if not path.exists():
                raise FileNotFoundError(f"文件不存在: {ctx.source_location}")
            if not path.is_file():
                raise IOError(f"路径不是文件: {ctx.source_location}")

            ctx.raw_bytes = path.read_bytes()
            ctx.metadata["file_size"] = len(ctx.raw_bytes)
            ctx.metadata["file_name"] = path.name

            # 根据扩展名检测文件类型
            ext = path.suffix.lower()
            ctx.file_type = _EXTENSION_MAP.get(ext, "txt")

            logger.info(
                "FetcherNode: 读取文件 %s (%d 字节, 类型=%s)",
                ctx.source_location,
                len(ctx.raw_bytes),
                ctx.file_type,
            )
        else:
            raise ValueError(f"不支持的来源类型: {source_type}")


# ---------------------------------------------------------------------------
# ParserNode —— 文档解析节点
# ---------------------------------------------------------------------------

class ParserNode(IngestionNode):
    """文档解析节点 —— 将原始文件内容解析为纯文本。

    支持的文件格式：
        - **pdf**: 使用 ``pdfplumber`` 提取文本
        - **docx**: 使用 ``python-docx`` 提取文本（需安装依赖）
        - **md / txt**: 直接读取文本内容
    """

    @property
    def node_type(self) -> str:
        """返回节点类型标识。"""
        return "parser"

    async def execute(
        self,
        ctx: IngestionContext,
        settings: dict[str, Any] | None = None,
    ) -> None:
        """解析文档内容为纯文本。

        Args:
            ctx:      管线执行上下文（需要 ``raw_bytes`` 和 ``file_type``）。
            settings: 节点配置（可选）。

        Raises:
            ValueError: 没有原始数据或文件类型未知。
        """
        if ctx.raw_bytes is None:
            raise ValueError("ParserNode: 上下文中没有原始数据（raw_bytes 为空）")
        if ctx.file_type is None:
            raise ValueError("ParserNode: 上下文中没有文件类型（file_type 为空）")

        parser_fn = {
            "pdf": self._parse_pdf,
            "docx": self._parse_docx,
            "md": self._parse_text,
            "txt": self._parse_text,
        }.get(ctx.file_type)

        if parser_fn is None:
            raise ValueError(f"ParserNode: 不支持的文件类型: {ctx.file_type}")

        ctx.plain_text = parser_fn(ctx.raw_bytes)
        ctx.metadata["text_length"] = len(ctx.plain_text) if ctx.plain_text else 0

        logger.info(
            "ParserNode: 解析完成, 文件类型=%s, 文本长度=%d",
            ctx.file_type,
            len(ctx.plain_text or ""),
        )

    @staticmethod
    def _parse_pdf(raw_bytes: bytes) -> str:
        """使用 pdfplumber 解析 PDF 文件。

        Args:
            raw_bytes: PDF 文件的原始字节内容。

        Returns:
            提取的纯文本内容。
        """
        import io
        import pdfplumber

        text_parts: list[str] = []
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
                logger.debug("ParserNode: PDF 第 %d/%d 页提取完成", i + 1, len(pdf.pages))

        return "\n\n".join(text_parts)

    @staticmethod
    def _parse_docx(raw_bytes: bytes) -> str:
        """使用 python-docx 解析 DOCX 文件。

        Args:
            raw_bytes: DOCX 文件的原始字节内容。

        Returns:
            提取的纯文本内容。
        """
        import io

        try:
            from docx import Document  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "解析 DOCX 文件需要安装 python-docx: pip install python-docx"
            )

        doc = Document(io.BytesIO(raw_bytes))
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n\n".join(paragraphs)

    @staticmethod
    def _parse_text(raw_bytes: bytes) -> str:
        """直接解码文本文件内容。

        尝试 UTF-8 编码，若失败则使用 latin-1 作为后备。

        Args:
            raw_bytes: 文本文件的原始字节内容。

        Returns:
            解码后的文本内容。
        """
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return raw_bytes.decode("latin-1")


# ---------------------------------------------------------------------------
# EnhancerNode —— LLM 文本增强节点
# ---------------------------------------------------------------------------

class EnhancerNode(IngestionNode):
    """LLM 文本增强节点 —— 可选节点，通过 LLM 提取关键词和增强文本。

    若未提供 ``llm_service``，则跳过增强步骤，直接使用原文。
    """

    @property
    def node_type(self) -> str:
        """返回节点类型标识。"""
        return "enhancer"

    def __init__(self, llm_service: Any | None = None) -> None:
        """初始化增强节点。

        Args:
            llm_service: LLM 服务实例（可选）。若为 ``None`` 则跳过增强。
        """
        self._llm_service = llm_service

    async def execute(
        self,
        ctx: IngestionContext,
        settings: dict[str, Any] | None = None,
    ) -> None:
        """对文本进行增强处理。

        当 ``llm_service`` 可用时，调用 LLM 提取关键词；
        否则执行基础关键词提取（基于文本频率）。

        Args:
            ctx:      管线执行上下文（需要 ``plain_text``）。
            settings: 节点配置（可选）。
        """
        if ctx.plain_text is None:
            raise ValueError("EnhancerNode: 上下文中没有文本（plain_text 为空）")

        if self._llm_service is not None:
            await self._enhance_with_llm(ctx, settings)
        else:
            self._basic_enhance(ctx)

        logger.info(
            "EnhancerNode: 增强完成, 关键词数量=%d",
            len(ctx.keywords),
        )

    async def _enhance_with_llm(
        self,
        ctx: IngestionContext,
        settings: dict[str, Any] | None,
    ) -> None:
        """使用 LLM 进行文本增强。

        Args:
            ctx:      管线执行上下文。
            settings: 节点配置。
        """
        max_keywords = (settings or {}).get("max_keywords", 10)

        # 截取前 2000 字符作为关键词提取输入
        sample = ctx.plain_text[:2000] if ctx.plain_text else ""
        prompt = (
            f"请从以下文本中提取不超过 {max_keywords} 个关键词，"
            f"以 JSON 数组格式返回，例如：[\"关键词1\", \"关键词2\"]。\n\n"
            f"文本内容：\n{sample}"
        )

        try:
            response = await self._llm_service.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=256,
            )
            import json

            # 尝试从响应中解析 JSON 数组
            match = re.search(r"\[.*?\]", response, re.DOTALL)
            if match:
                ctx.keywords = json.loads(match.group())
            else:
                ctx.keywords = self._extract_basic_keywords(ctx.plain_text)
        except Exception as exc:
            logger.warning("EnhancerNode: LLM 调用失败，降级为基础提取: %s", exc)
            ctx.keywords = self._extract_basic_keywords(ctx.plain_text)

    def _basic_enhance(self, ctx: IngestionContext) -> None:
        """基础增强：不使用 LLM，仅提取关键词。

        Args:
            ctx: 管线执行上下文。
        """
        ctx.keywords = self._extract_basic_keywords(ctx.plain_text)

    @staticmethod
    def _extract_basic_keywords(text: str | None) -> list[str]:
        """基于词频的简单关键词提取。

        Args:
            text: 待提取关键词的文本。

        Returns:
            关键词列表（最多 10 个）。
        """
        if not text:
            return []

        # 简单分词：按空白和标点分割，过滤短词
        words = re.findall(r"[a-zA-Z\u4e00-\u9fff]{2,}", text)

        # 统计词频
        freq: dict[str, int] = {}
        for word in words:
            w = word.lower()
            freq[w] = freq.get(w, 0) + 1

        # 按频率排序取前 10
        sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        return [w for w, _ in sorted_words[:10]]


# ---------------------------------------------------------------------------
# ChunkerNode —— 文本分块节点
# ---------------------------------------------------------------------------

class ChunkerNode(IngestionNode):
    """文本分块节点 —— 将长文本切分为固定大小或结构感知的块。

    支持两种策略：
        - ``fixed``: 按字符数切分，支持重叠（默认 500 字符，50 重叠）
        - ``structure``: 按标题标记（``##``）切分

    Settings:
        strategy:    分块策略，``"fixed"`` 或 ``"structure"``，默认 ``"fixed"``。
        chunk_size:  固定大小策略的块大小（字符数），默认 500。
        overlap:     固定大小策略的重叠大小（字符数），默认 50。
    """

    @property
    def node_type(self) -> str:
        """返回节点类型标识。"""
        return "chunker"

    async def execute(
        self,
        ctx: IngestionContext,
        settings: dict[str, Any] | None = None,
    ) -> None:
        """对文本进行分块处理。

        根据配置的策略（固定大小或结构感知）将文本切分为多个块，
        并计算每个块的字符数、近似 token 数和内容哈希。

        Args:
            ctx:      管线执行上下文（需要 ``plain_text`` 或 ``enhanced_text``）。
            settings: 节点配置（可选）。
        """
        text = ctx.enhanced_text or ctx.plain_text
        if not text:
            raise ValueError("ChunkerNode: 上下文中没有可分块的文本")

        settings = settings or {}
        strategy = settings.get("strategy", "fixed")

        if strategy == "fixed":
            chunks = self._fixed_size_split(
                text,
                chunk_size=settings.get("chunk_size", 500),
                overlap=settings.get("overlap", 50),
            )
        elif strategy == "structure":
            chunks = self._structure_aware_split(text)
        else:
            raise ValueError(f"ChunkerNode: 不支持的分块策略: {strategy}")

        ctx.chunks = chunks
        ctx.metadata["chunk_strategy"] = strategy
        ctx.metadata["chunk_count"] = len(chunks)

        logger.info(
            "ChunkerNode: 分块完成, 策略=%s, 分块数量=%d",
            strategy,
            len(chunks),
        )

    @staticmethod
    def _fixed_size_split(
        text: str,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> list[ChunkData]:
        """固定大小分块策略。

        将文本按指定字符数切分为多个块，相邻块之间有重叠。

        Args:
            text:       待分块的文本。
            chunk_size: 每个块的最大字符数。
            overlap:    相邻块之间的重叠字符数。

        Returns:
            分块数据列表。
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size 必须大于 0，当前值: {chunk_size}")
        if overlap < 0:
            raise ValueError(f"overlap 不能为负数，当前值: {overlap}")
        if overlap >= chunk_size:
            raise ValueError(f"overlap ({overlap}) 必须小于 chunk_size ({chunk_size})")

        chunks: list[ChunkData] = []
        step = chunk_size - overlap
        start = 0
        index = 0

        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk_text = text[start:end]

            chunks.append(ChunkData(
                content=chunk_text,
                index=index,
                char_count=len(chunk_text),
                token_count=len(chunk_text) // 4,
                content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            ))

            index += 1
            start += step

        return chunks

    @staticmethod
    def _structure_aware_split(text: str) -> list[ChunkData]:
        """结构感知分块策略。

        按 Markdown 标题标记（``##`` 或 ``#``）将文本切分为块。
        若没有找到标题标记，则回退到固定大小策略。

        Args:
            text: 待分块的文本。

        Returns:
            分块数据列表。
        """
        # 匹配 # 或 ## 开头的标题行
        heading_pattern = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)
        matches = list(heading_pattern.finditer(text))

        if not matches:
            # 没有标题标记，回退到固定大小策略
            return ChunkerNode._fixed_size_split(text)

        chunks: list[ChunkData] = []
        boundaries = [0] + [m.start() for m in matches] + [len(text)]

        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            chunk_text = text[start:end].strip()

            if chunk_text:
                chunks.append(ChunkData(
                    content=chunk_text,
                    index=i,
                    char_count=len(chunk_text),
                    token_count=len(chunk_text) // 4,
                    content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                ))

        return chunks


# ---------------------------------------------------------------------------
# EnricherNode —— 分块增强节点
# ---------------------------------------------------------------------------

class EnricherNode(IngestionNode):
    """分块增强节点 —— 可选节点，为每个分块提取关键词和摘要。

    当 ``llm_service`` 可用时，调用 LLM 进行增强；
    否则进行基础的摘要截取和关键词继承。
    """

    @property
    def node_type(self) -> str:
        """返回节点类型标识。"""
        return "enricher"

    def __init__(self, llm_service: Any | None = None) -> None:
        """初始化增强节点。

        Args:
            llm_service: LLM 服务实例（可选）。
        """
        self._llm_service = llm_service

    async def execute(
        self,
        ctx: IngestionContext,
        settings: dict[str, Any] | None = None,
    ) -> None:
        """对每个分块进行增强处理。

        Args:
            ctx:      管线执行上下文（需要 ``chunks``）。
            settings: 节点配置（可选）。
        """
        if not ctx.chunks:
            logger.warning("EnricherNode: 没有分块需要增强")
            return

        for chunk in ctx.chunks:
            if self._llm_service is not None:
                await self._enrich_chunk_with_llm(chunk, settings)
            else:
                self._basic_enrich_chunk(chunk)

        logger.info("EnricherNode: 增强完成, 分块数量=%d", len(ctx.chunks))

    async def _enrich_chunk_with_llm(
        self,
        chunk: ChunkData,
        settings: dict[str, Any] | None,
    ) -> None:
        """使用 LLM 为单个分块提取关键词和摘要。

        Args:
            chunk:    待增强的分块数据。
            settings: 节点配置。
        """
        import json

        sample = chunk.content[:500]
        prompt = (
            "请为以下文本片段提取 3-5 个关键词和一句话摘要。\n"
            '以 JSON 格式返回：{"keywords": ["词1", "词2"], "summary": "摘要内容"}\n\n'
            f"文本：\n{sample}"
        )

        try:
            response = await self._llm_service.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )
            match = re.search(r"\{.*?\}", response, re.DOTALL)
            if match:
                result = json.loads(match.group())
                chunk.keywords = result.get("keywords", [])
                chunk.summary = result.get("summary")
        except Exception as exc:
            logger.warning("EnricherNode: LLM 调用失败，降级为基础增强: %s", exc)
            self._basic_enrich_chunk(chunk)

    @staticmethod
    def _basic_enrich_chunk(chunk: ChunkData) -> None:
        """基础分块增强：截取前 100 字符作为摘要。

        Args:
            chunk: 待增强的分块数据。
        """
        if not chunk.summary:
            chunk.summary = chunk.content[:100].strip() + "..." if len(chunk.content) > 100 else chunk.content
        if not chunk.keywords:
            chunk.keywords = []


# ---------------------------------------------------------------------------
# IndexerNode —— 索引写入节点
# ---------------------------------------------------------------------------

class IndexerNode(IngestionNode):
    """索引写入节点 —— 将分块数据向量化并写入 PostgreSQL（pgvector）。

    使用 ``embedding_service`` 对分块文本进行向量化，然后通过 asyncpg
    将 chunk 记录（含 embedding 向量）批量插入 ``t_knowledge_chunk`` 表。
    需要上下文提供 ``kb_id`` 和 ``doc_id`` 以正确关联知识库和文档。
    """

    @property
    def node_type(self) -> str:
        """返回节点类型标识。"""
        return "indexer"

    def __init__(
        self,
        embedding_service: Any | None = None,
    ) -> None:
        """初始化索引节点。

        Args:
            embedding_service: 向量嵌入服务实例（可选）。
        """
        self._embedding_service = embedding_service

    async def execute(
        self,
        ctx: IngestionContext,
        settings: dict[str, Any] | None = None,
    ) -> None:
        """将分块向量化并写入数据库。

        流程：
            1. 检查上下文中是否有分块和必要的 kb_id / doc_id
            2. 使用 embedding_service 对分块文本进行批量向量化
            3. 通过 asyncpg 批量插入 t_knowledge_chunk 表

        Args:
            ctx:      管线执行上下文（需要 ``chunks``, ``kb_id``, ``doc_id``）。
            settings: 节点配置（可选）。
        """
        if not ctx.chunks:
            logger.warning("IndexerNode: 没有分块需要索引")
            return

        kb_id = ctx.kb_id
        doc_id = ctx.doc_id
        if kb_id is None or doc_id is None:
            logger.warning(
                "IndexerNode: 缺少 kb_id 或 doc_id，跳过数据库写入 "
                "(kb_id=%s, doc_id=%s)",
                kb_id, doc_id,
            )
            # 仍然做向量化，只是不写 DB
            await self._vectorize_chunks(ctx)
            return

        # 向量化
        await self._vectorize_chunks(ctx)

        # 批量写入数据库
        await self._write_chunks_to_db(ctx, kb_id, doc_id)

    async def _vectorize_chunks(self, ctx: IngestionContext) -> None:
        """对分块进行向量化。

        如果未通过构造函数提供 embedding_service，则自动创建一个。

        Args:
            ctx: 管线执行上下文。
        """
        if self._embedding_service is None:
            # 延迟初始化 embedding_service
            try:
                from ragent.infra.ai.embedding_service import EmbeddingService
                from ragent.infra.ai.models import ModelConfigManager
                from ragent.infra.ai.model_selector import ModelSelector

                config_manager = ModelConfigManager()
                selector = ModelSelector(config_manager)
                self._embedding_service = EmbeddingService(config_manager, selector)
                logger.info("IndexerNode: 自动初始化 embedding_service")
            except Exception as exc:
                logger.warning("IndexerNode: 无法初始化 embedding_service: %s", exc)
                return

        texts = [chunk.content for chunk in ctx.chunks]
        try:
            vectors = await self._embedding_service.embed_batch(texts)
            for chunk, vector in zip(ctx.chunks, vectors):
                chunk.vector = vector
            logger.info("IndexerNode: 向量化完成, 分块数量=%d", len(ctx.chunks))
        except Exception as exc:
            logger.warning("IndexerNode: 向量化失败: %s", exc)

    @staticmethod
    async def _write_chunks_to_db(
        ctx: IngestionContext,
        kb_id: int,
        doc_id: int,
    ) -> None:
        """将分块 + embedding 批量写入 t_knowledge_chunk 表。

        使用 SQLAlchemy ORM 批量插入，保持代码库风格一致。

        Args:
            ctx:    管线执行上下文（需要 ``chunks``）。
            kb_id:  知识库 ID。
            doc_id: 文档 ID。
        """
        import json

        from ragent.common.models import KnowledgeChunk
        from ragent.common.snowflake import generate_id
        from ragent.infra.database import get_session_factory, init_db

        # 确保数据库已初始化（Celery worker 可能尚未初始化）
        try:
            await init_db()
        except Exception:
            # 可能已初始化，忽略重复初始化错误
            pass

        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                # 批量创建 KnowledgeChunk 对象
                chunk_objects = []
                for chunk in ctx.chunks:
                    chunk_id = chunk.metadata.get("snowflake_id")
                    if chunk_id is None:
                        chunk_id = generate_id()

                    # keywords 列表转为 JSON 字符串
                    keywords_json = json.dumps(chunk.keywords) if chunk.keywords else None

                    chunk_obj = KnowledgeChunk(
                        id=chunk_id,
                        kb_id=kb_id,
                        doc_id=doc_id,
                        content=chunk.content,
                        chunk_index=chunk.index,
                        char_count=chunk.char_count,
                        token_count=chunk.token_count,
                        content_hash=chunk.content_hash,
                        keywords=keywords_json,
                        summary=chunk.summary,
                        enabled=True,
                        embedding=chunk.vector,  # pgvector Vector 列直接接受 list[float]
                    )
                    chunk_objects.append(chunk_obj)

                # 批量插入
                session.add_all(chunk_objects)
                await session.commit()

                logger.info(
                    "IndexerNode: 写入 %d 个分块到数据库, kb_id=%d, doc_id=%d",
                    len(ctx.chunks), kb_id, doc_id,
                )

            except Exception as exc:
                logger.error("IndexerNode: 数据库写入失败: %s", exc)
                await session.rollback()
                raise

        # 在元数据中记录索引信息
        ctx.metadata["indexed_chunks"] = len(ctx.chunks)
        ctx.metadata["index_status"] = "success"


# ---------------------------------------------------------------------------
# 节点注册表
# ---------------------------------------------------------------------------

NODE_REGISTRY: dict[str, type[IngestionNode]] = {
    "fetcher": FetcherNode,
    "parser": ParserNode,
    "enhancer": EnhancerNode,
    "chunker": ChunkerNode,
    "enricher": EnricherNode,
    "indexer": IndexerNode,
}
"""节点类型注册表 —— 映射 ``node_type`` 到对应的节点类。"""


def get_node(node_type: str) -> IngestionNode:
    """根据节点类型创建节点实例。

    Args:
        node_type: 节点类型标识字符串。

    Returns:
        节点实例。

    Raises:
        ValueError: 未知的节点类型。
    """
    cls = NODE_REGISTRY.get(node_type)
    if cls is None:
        raise ValueError(f"未知的节点类型: {node_type!r}，可用类型: {list(NODE_REGISTRY.keys())}")
    return cls()
