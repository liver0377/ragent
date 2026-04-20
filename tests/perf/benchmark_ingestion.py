"""
文档摄取管线性能基准测试。

测试场景：
    1. test_benchmark_pdf_parsing — PDF 解析性能（用真实测试 PDF）
    2. test_benchmark_chunking — 文本分块性能（生成大量文本测试）
    3. test_benchmark_full_pipeline — 完整摄取管线性能
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

import pytest

from ragent.infra.ai.models import ModelCandidate, ModelConfig, ModelConfigManager
from ragent.infra.ai.model_selector import ModelSelector
from ragent.ingestion.context import IngestionContext
from ragent.ingestion.nodes import (
    ChunkerNode,
    EnhancerNode,
    FetcherNode,
    IndexerNode,
    ParserNode,
)
from ragent.ingestion.pipeline import IngestionPipeline, NodeConfig


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "pdfs"
ITERATIONS = 3


def _compute_stats(times: list[float]) -> dict[str, float]:
    """根据耗时列表计算统计指标。"""
    sorted_times = sorted(times)
    n = len(sorted_times)
    total = sum(sorted_times)
    return {
        "avg_ms": (total / n) * 1000,
        "p50_ms": sorted_times[int(n * 0.50)] * 1000,
        "p95_ms": sorted_times[int(n * 0.95)] * 1000,
        "p99_ms": sorted_times[int(n * 0.99)] * 1000,
        "min_ms": sorted_times[0] * 1000,
        "max_ms": sorted_times[-1] * 1000,
        "ops_per_sec": n / total if total > 0 else 0,
    }


def _print_stats(name: str, stats: dict[str, float]) -> None:
    """打印性能统计信息。"""
    print(f"\n{'=' * 60}")
    print(f"  Benchmark: {name}")
    print(f"{'=' * 60}")
    print(f"  Avg:   {stats['avg_ms']:.4f} ms")
    print(f"  P50:   {stats['p50_ms']:.4f} ms")
    print(f"  P95:   {stats['p95_ms']:.4f} ms")
    print(f"  P99:   {stats['p99_ms']:.4f} ms")
    print(f"  Min:   {stats['min_ms']:.4f} ms")
    print(f"  Max:   {stats['max_ms']:.4f} ms")
    print(f"  Ops/s: {stats['ops_per_sec']:.1f}")
    print(f"{'=' * 60}")


def _generate_large_text(size_chars: int = 50_000) -> str:
    """生成指定大小的测试文本。"""
    paragraph = (
        "RAG（检索增强生成）是一种将信息检索与文本生成相结合的技术。"
        "它通过检索外部知识库中的相关文档片段，将其作为上下文提供给大语言模型，"
        "从而提升生成内容的准确性和可靠性。RAG 系统通常包含检索器和生成器两个核心组件，"
        "检索器负责从知识库中找到与用户问题最相关的文档，生成器则基于检索到的内容生成回答。"
    )
    text = paragraph
    while len(text) < size_chars:
        text += "\n\n" + paragraph
    return text[:size_chars]


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


async def test_benchmark_pdf_parsing() -> None:
    """PDF 解析性能（用真实测试 PDF）。"""
    pdf_files = list(FIXTURES_DIR.glob("*.pdf"))
    assert len(pdf_files) >= 1, f"测试 PDF 文件不存在: {FIXTURES_DIR}"

    pdf_path = pdf_files[0]
    parser = ParserNode()

    raw_bytes = pdf_path.read_bytes()
    file_type = "pdf"

    # Warmup
    ctx_warmup = IngestionContext(
        task_id=1,
        pipeline_id=1,
        source_type="local",
        source_location=str(pdf_path),
    )
    ctx_warmup.raw_bytes = raw_bytes
    ctx_warmup.file_type = file_type
    for _ in range(3):
        ctx_w = IngestionContext(
            task_id=1, pipeline_id=1, source_type="local", source_location=str(pdf_path)
        )
        ctx_w.raw_bytes = raw_bytes
        ctx_w.file_type = file_type
        await parser.execute(ctx_w)

    # Benchmark
    times: list[float] = []
    for i in range(ITERATIONS):
        ctx = IngestionContext(
            task_id=i + 100, pipeline_id=1, source_type="local", source_location=str(pdf_path)
        )
        ctx.raw_bytes = raw_bytes
        ctx.file_type = file_type
        t0 = time.perf_counter()
        await parser.execute(ctx)
        t1 = time.perf_counter()
        times.append(t1 - t0)

    stats = _compute_stats(times)
    _print_stats(f"PDFParsing ({pdf_path.name}, {len(raw_bytes)} bytes)", stats)
    assert stats["avg_ms"] < 60000, f"PDF 解析平均耗时不应超过 60s"


async def test_benchmark_chunking() -> None:
    """文本分块性能（生成大量文本测试）。"""
    chunker = ChunkerNode()

    # 不同规模的文本
    test_cases = [
        ("small_5k", 5_000),
        ("medium_50k", 50_000),
    ]

    for case_name, text_size in test_cases:
        text = _generate_large_text(text_size)
        ctx = IngestionContext(
            task_id=1, pipeline_id=1, source_type="local", source_location="test.txt"
        )
        ctx.plain_text = text

        # Warmup
        for _ in range(3):
            ctx_w = IngestionContext(
                task_id=1, pipeline_id=1, source_type="local", source_location="test.txt"
            )
            ctx_w.plain_text = text
            await chunker.execute(ctx_w)

        # Benchmark
        times: list[float] = []
        for i in range(ITERATIONS):
            ctx_b = IngestionContext(
                task_id=i + 100, pipeline_id=1, source_type="local", source_location="test.txt"
            )
            ctx_b.plain_text = text
            t0 = time.perf_counter()
            await chunker.execute(ctx_b)
            t1 = time.perf_counter()
            times.append(t1 - t0)

        stats = _compute_stats(times)
        _print_stats(f"Chunking ({case_name}, {text_size} chars)", stats)
        assert stats["avg_ms"] < 200, f"分块平均耗时不应超过 200ms ({case_name})"


async def test_benchmark_full_pipeline() -> None:
    """完整摄取管线性能。"""
    pdf_files = list(FIXTURES_DIR.glob("*.pdf"))
    assert len(pdf_files) >= 1, f"测试 PDF 文件不存在: {FIXTURES_DIR}"
    pdf_path = pdf_files[0]

    nodes = [
        NodeConfig(node_id="n1", node_type="fetcher", next_node_id="n2"),
        NodeConfig(node_id="n2", node_type="parser", next_node_id="n3"),
        NodeConfig(node_id="n3", node_type="enhancer", next_node_id="n4"),
        NodeConfig(node_id="n4", node_type="chunker", next_node_id="n5"),
        NodeConfig(node_id="n5", node_type="enricher", next_node_id="n6"),
        NodeConfig(node_id="n6", node_type="indexer"),
    ]

    pipeline = IngestionPipeline(nodes)
    pipeline.validate()

    # Warmup
    for _ in range(3):
        ctx_w = IngestionContext(
            task_id=1, pipeline_id=1, source_type="local", source_location=str(pdf_path)
        )
        await pipeline.execute(ctx_w)

    # Benchmark
    times: list[float] = []
    for i in range(ITERATIONS):
        ctx = IngestionContext(
            task_id=i + 100, pipeline_id=1, source_type="local", source_location=str(pdf_path)
        )
        t0 = time.perf_counter()
        await pipeline.execute(ctx)
        t1 = time.perf_counter()
        times.append(t1 - t0)
        assert ctx.status == "COMPLETED", f"管线应成功完成: {ctx.error_message}"

    stats = _compute_stats(times)
    _print_stats(f"FullIngestionPipeline ({pdf_path.name})", stats)
    assert stats["avg_ms"] < 120000, "完整摄取管线平均耗时不应超过 120s"
