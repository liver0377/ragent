"""
文档摄取管线模块 —— 将文档处理为向量可检索的分块。

核心组件：
    - :class:`IngestionContext`  管线执行上下文
    - :class:`ChunkData`        文本分块数据
    - :class:`IngestionPipeline` 管线执行引擎
    - :class:`NodeConfig`       节点配置
    - :class:`IngestionNode`    节点抽象基类
    - 具体节点：FetcherNode, ParserNode, EnhancerNode, ChunkerNode, EnricherNode, IndexerNode

典型用法::

    from ragent.ingestion import (
        IngestionContext, IngestionPipeline, NodeConfig,
    )

    nodes = [
        NodeConfig(node_id="n1", node_type="fetcher", next_node_id="n2"),
        NodeConfig(node_id="n2", node_type="parser", next_node_id="n3"),
        NodeConfig(node_id="n3", node_type="chunker"),
    ]

    pipeline = IngestionPipeline(nodes)
    ctx = IngestionContext(
        task_id=123456,
        pipeline_id=1,
        source_type="local",
        source_location="/path/to/document.pdf",
    )
    result = await pipeline.execute(ctx)
"""

from ragent.ingestion.context import (
    ChunkData,
    IngestionContext,
    PENDING,
    RUNNING,
    COMPLETED,
    FAILED,
)
from ragent.ingestion.nodes import (
    FetcherNode,
    ParserNode,
    EnhancerNode,
    ChunkerNode,
    EnricherNode,
    IndexerNode,
    IngestionNode,
    NODE_REGISTRY,
    get_node,
)
from ragent.ingestion.pipeline import (
    NodeConfig,
    NodeExecutionRecord,
    IngestionPipeline,
)

__all__ = [
    # 上下文
    "IngestionContext",
    "ChunkData",
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    # 节点
    "IngestionNode",
    "FetcherNode",
    "ParserNode",
    "EnhancerNode",
    "ChunkerNode",
    "EnricherNode",
    "IndexerNode",
    "NODE_REGISTRY",
    "get_node",
    # 管线
    "NodeConfig",
    "NodeExecutionRecord",
    "IngestionPipeline",
]
