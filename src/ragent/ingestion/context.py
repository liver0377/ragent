"""
文档摄取管线上下文模块 —— 承载管线执行状态的数据容器。

核心职责：
    1. 定义 :class:`ChunkData` 数据类，表示文本分块及其向量
    2. 定义 :class:`IngestionContext` 类，在管线节点间传递执行状态
    3. 提供状态常量（PENDING / RUNNING / COMPLETED / FAILED）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 状态常量
# ---------------------------------------------------------------------------

PENDING: str = "PENDING"
"""管线任务尚未开始执行。"""

RUNNING: str = "RUNNING"
"""管线任务正在执行中。"""

COMPLETED: str = "COMPLETED"
"""管线任务已成功完成。"""

FAILED: str = "FAILED"
"""管线任务执行失败。"""


# ---------------------------------------------------------------------------
# ChunkData 数据类
# ---------------------------------------------------------------------------

@dataclass
class ChunkData:
    """文本分块数据。

    表示文档经过分块策略切割后的一段文本，携带索引、字符数、
    近似 token 数、内容哈希、关键词、摘要及向量嵌入。

    Attributes:
        content:      分块文本内容。
        index:        分块在原始文档中的序号（从 0 开始）。
        char_count:   分块文本的字符数。
        token_count:  近似 token 数（默认 ``len(content) // 4``）。
        content_hash: 分块内容的 SHA-256 哈希摘要（十六进制）。
        keywords:     从分块中提取的关键词列表。
        summary:      分块内容的简要摘要（可选）。
        vector:       分块文本的向量嵌入表示（可选）。
        metadata:     附加元数据字典。
    """

    content: str
    index: int
    char_count: int
    token_count: int | None = None
    content_hash: str = ""
    keywords: list[str] = field(default_factory=list)
    summary: str | None = None
    vector: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """初始化后自动计算 char_count、token_count 和 content_hash。"""
        if self.char_count == 0 and self.content:
            self.char_count = len(self.content)
        if self.token_count is None and self.content:
            self.token_count = len(self.content) // 4
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(
                self.content.encode("utf-8")
            ).hexdigest()


# ---------------------------------------------------------------------------
# IngestionContext 上下文类
# ---------------------------------------------------------------------------

class IngestionContext:
    """文档摄取管线执行上下文。

    在管线执行过程中，上下文对象在各个节点之间传递，承载所有中间状态。
    每个节点读取输入字段、处理并写入输出字段。

    Attributes:
        task_id:          Snowflake 任务 ID。
        pipeline_id:      使用的管线配置 ID。
        source_type:      来源类型，``"local"`` / ``"http"`` / ``"s3"``。
        source_location:  文件路径或 URL。
        raw_bytes:        读取到的原始字节内容。
        file_type:        检测到的文件类型（pdf, docx, md, txt）。
        plain_text:       解析后的纯文本内容。
        enhanced_text:    LLM 增强后的文本内容。
        keywords:         从文档中提取的关键词列表。
        chunks:           分块结果列表，每项为 :class:`ChunkData`。
        metadata:         灵活的元数据存储字典。
        status:           执行状态（PENDING / RUNNING / COMPLETED / FAILED）。
        error_message:    错误信息（仅在状态为 FAILED 时有值）。
    """

    def __init__(
        self,
        task_id: int,
        pipeline_id: int,
        source_type: str,
        source_location: str,
    ) -> None:
        """初始化摄取管线上下文。

        Args:
            task_id:          Snowflake 任务 ID。
            pipeline_id:      管线配置 ID。
            source_type:      来源类型。
            source_location:  文件路径或 URL。
        """
        self.task_id: int = task_id
        self.pipeline_id: int = pipeline_id
        self.source_type: str = source_type
        self.source_location: str = source_location

        # 中间数据
        self.raw_bytes: bytes | None = None
        self.file_type: str | None = None
        self.plain_text: str | None = None
        self.enhanced_text: str | None = None
        self.keywords: list[str] = []
        self.chunks: list[ChunkData] = []

        # 元数据与状态
        self.metadata: dict[str, Any] = {}
        self.status: str = PENDING
        self.error_message: str | None = None

    def mark_running(self) -> None:
        """将上下文状态标记为运行中。"""
        self.status = RUNNING

    def mark_completed(self) -> None:
        """将上下文状态标记为已完成。"""
        self.status = COMPLETED

    def mark_failed(self, error_message: str) -> None:
        """将上下文状态标记为失败并记录错误信息。

        Args:
            error_message: 错误描述信息。
        """
        self.status = FAILED
        self.error_message = error_message

    def __repr__(self) -> str:
        """返回上下文的简洁字符串表示。"""
        return (
            f"IngestionContext(task_id={self.task_id}, "
            f"pipeline_id={self.pipeline_id}, "
            f"source_type={self.source_type!r}, "
            f"status={self.status}, "
            f"chunks={len(self.chunks)})"
        )
