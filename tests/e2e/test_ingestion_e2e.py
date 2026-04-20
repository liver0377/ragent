"""
E2E 测试 —— 文档摄取管线端到端测试。

测试场景：
    1. test_full_ingestion_pipeline — fetcher→parser→chunker 完整管线
    2. test_ingestion_with_enhancer — 加上 enhancer 节点的管线
    3. test_ingestion_pipeline_validation — 环检测、无效节点引用
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ragent.ingestion.context import COMPLETED, IngestionContext
from ragent.ingestion.pipeline import IngestionPipeline, NodeConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "pdfs"


@pytest.fixture
def sample_pdf_path() -> str:
    """返回测试 PDF 文件的绝对路径。"""
    pdf_path = FIXTURES_DIR / "rag-survey-2024.pdf"
    assert pdf_path.exists(), f"测试 PDF 不存在: {pdf_path}"
    return str(pdf_path)


@pytest.fixture
def ingestion_context(sample_pdf_path: str) -> IngestionContext:
    """创建用于测试的摄取上下文。"""
    return IngestionContext(
        task_id=1001,
        pipeline_id=1,
        source_type="local",
        source_location=sample_pdf_path,
    )


@pytest.fixture
def basic_pipeline() -> IngestionPipeline:
    """创建 fetcher→parser→chunker 基础管线。"""
    nodes = [
        NodeConfig(node_id="fetcher_1", node_type="fetcher", next_node_id="parser_1"),
        NodeConfig(node_id="parser_1", node_type="parser", next_node_id="chunker_1"),
        NodeConfig(node_id="chunker_1", node_type="chunker"),
    ]
    return IngestionPipeline(nodes)


@pytest.fixture
def pipeline_with_enhancer() -> IngestionPipeline:
    """创建 fetcher→parser→enhancer→chunker 管线。"""
    nodes = [
        NodeConfig(node_id="fetcher_1", node_type="fetcher", next_node_id="parser_1"),
        NodeConfig(node_id="parser_1", node_type="parser", next_node_id="enhancer_1"),
        NodeConfig(node_id="enhancer_1", node_type="enhancer", next_node_id="chunker_1"),
        NodeConfig(node_id="chunker_1", node_type="chunker"),
    ]
    return IngestionPipeline(nodes)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


async def test_full_ingestion_pipeline(
    basic_pipeline: IngestionPipeline,
    ingestion_context: IngestionContext,
) -> None:
    """用 tests/fixtures/pdfs/ 下的真实 PDF，跑 fetcher→parser→chunker 管线，验证 chunks 不为空。"""
    # 验证管线配置
    basic_pipeline.validate()

    # 执行管线（真实的文件读取和 PDF 解析，不 mock）
    result = await basic_pipeline.execute(ingestion_context)

    # 验证执行成功
    assert result.status == COMPLETED, f"管线执行失败: {result.error_message}"

    # 验证 raw_bytes 已被读取
    assert result.raw_bytes is not None
    assert len(result.raw_bytes) > 0

    # 验证文件类型被正确检测
    assert result.file_type == "pdf"

    # 验证 plain_text 已被解析
    assert result.plain_text is not None
    assert len(result.plain_text) > 0

    # 验证 chunks 不为空
    assert len(result.chunks) > 0, "分块结果不应为空"

    # 验证每个 chunk 的基本属性
    for chunk in result.chunks:
        assert chunk.content, "chunk 内容不应为空"
        assert chunk.char_count > 0, "chunk 字符数应大于 0"
        assert chunk.content_hash, "chunk 应有 content_hash"


async def test_ingestion_with_enhancer(
    pipeline_with_enhancer: IngestionPipeline,
    ingestion_context: IngestionContext,
) -> None:
    """加上 enhancer 节点的管线。enhancer 在无 LLM 时使用基础关键词提取。"""
    # 验证管线配置
    pipeline_with_enhancer.validate()

    # 执行管线
    result = await pipeline_with_enhancer.execute(ingestion_context)

    # 验证执行成功
    assert result.status == COMPLETED, f"管线执行失败: {result.error_message}"

    # 验证 enhancer 提取了关键词（基础提取模式下也会提取）
    assert len(result.keywords) > 0, "enhancer 应提取关键词"

    # 验证 chunks 也不为空
    assert len(result.chunks) > 0, "分块结果不应为空"

    # 验证执行记录中有 enhancer 节点
    records = pipeline_with_enhancer.execution_records
    node_types = [r.node_type for r in records]
    assert "enhancer" in node_types


def test_ingestion_pipeline_validation() -> None:
    """管线验证：环检测和无效节点引用。"""
    # --- 测试无效节点引用 ---
    with pytest.raises(ValueError, match="next_node_id.*不存在"):
        nodes_invalid = [
            NodeConfig(node_id="n1", node_type="fetcher", next_node_id="nonexistent"),
        ]
        pipeline = IngestionPipeline(nodes_invalid)
        pipeline.validate()

    # --- 测试环检测 ---
    with pytest.raises(ValueError, match="循环引用"):
        nodes_cycle = [
            NodeConfig(node_id="n1", node_type="fetcher", next_node_id="n2"),
            NodeConfig(node_id="n2", node_type="parser", next_node_id="n1"),
        ]
        pipeline = IngestionPipeline(nodes_cycle)
        pipeline.validate()

    # --- 测试自环 ---
    with pytest.raises(ValueError, match="循环引用"):
        nodes_self_cycle = [
            NodeConfig(node_id="n1", node_type="fetcher", next_node_id="n1"),
        ]
        pipeline = IngestionPipeline(nodes_self_cycle)
        pipeline.validate()

    # --- 测试合法管线应通过验证 ---
    nodes_valid = [
        NodeConfig(node_id="n1", node_type="fetcher", next_node_id="n2"),
        NodeConfig(node_id="n2", node_type="parser", next_node_id="n3"),
        NodeConfig(node_id="n3", node_type="chunker"),
    ]
    pipeline = IngestionPipeline(nodes_valid)
    pipeline.validate()  # 不应抛出异常
