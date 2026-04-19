"""
文档摄取管线执行引擎 —— 编排节点链式执行。

核心职责：
    1. 定义 :class:`NodeConfig` 数据类，描述单个节点的配置
    2. 定义 :class:`IngestionPipeline` 管线执行引擎
    3. 管理节点的链式执行、条件跳过、错误处理和耗时记录
    4. 提供管线验证（环检测）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ragent.ingestion.context import RUNNING, IngestionContext
from ragent.ingestion.nodes import NODE_REGISTRY, IngestionNode, get_node

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NodeConfig 数据类
# ---------------------------------------------------------------------------

@dataclass
class NodeConfig:
    """管线节点配置。

    表示管线中一个节点的完整配置信息，包括节点标识、类型、
    下一个节点的 ID、以及可选的配置参数和条件。

    Attributes:
        node_id:       节点唯一标识。
        node_type:     节点类型（如 ``"fetcher"``、``"parser"``）。
        next_node_id:  下一个要执行的节点 ID，``None`` 表示链尾。
        settings_json: 节点配置参数字典（可选）。
        condition_json:节点执行条件字典（可选）。
    """

    node_id: str
    node_type: str
    next_node_id: str | None = None
    settings_json: dict[str, Any] | None = None
    condition_json: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# NodeExecutionRecord 记录
# ---------------------------------------------------------------------------

@dataclass
class NodeExecutionRecord:
    """节点执行记录。

    记录单个节点在管线执行过程中的耗时和状态。

    Attributes:
        node_id:     节点唯一标识。
        node_type:   节点类型。
        start_time:  开始执行的 monotonic 时间。
        end_time:    结束执行的 monotonic 时间。
        duration_ms: 执行耗时（毫秒）。
        status:      执行状态（``"ok"`` 或 ``"skipped"`` 或 ``"error"``）。
        error:       错误信息（可选）。
    """

    node_id: str
    node_type: str
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    status: str = "ok"
    error: str | None = None


# ---------------------------------------------------------------------------
# IngestionPipeline 管线执行引擎
# ---------------------------------------------------------------------------

class IngestionPipeline:
    """文档摄取管线执行引擎。

    根据有序的节点配置列表，构建链式执行流程。从第一个节点开始，
    依次执行每个节点，通过 ``next_node_id`` 连接下一个节点。

    支持：
        - 条件跳过：根据 ``condition_json`` 决定是否执行节点
        - 错误处理：捕获节点异常并设置上下文状态为 FAILED
        - 耗时记录：记录每个节点的执行耗时
        - 环检测：验证节点链中不存在循环引用

    用法::

        nodes = [
            NodeConfig(node_id="n1", node_type="fetcher", next_node_id="n2"),
            NodeConfig(node_id="n2", node_type="parser", next_node_id="n3"),
            NodeConfig(node_id="n3", node_type="chunker"),
        ]
        pipeline = IngestionPipeline(nodes)
        pipeline.validate()  # 检查是否有环
        result = await pipeline.execute(ctx)
    """

    def __init__(self, nodes: list[NodeConfig]) -> None:
        """初始化管线引擎。

        Args:
            nodes: 有序的节点配置列表。第一个节点作为起始节点。

        Raises:
            ValueError: 节点列表为空。
        """
        if not nodes:
            raise ValueError("管线节点列表不能为空")

        self._nodes: list[NodeConfig] = nodes
        self._node_map: dict[str, NodeConfig] = {n.node_id: n for n in nodes}
        self._execution_records: list[NodeExecutionRecord] = []

    @property
    def execution_records(self) -> list[NodeExecutionRecord]:
        """获取本次执行的节点耗时记录列表。"""
        return list(self._execution_records)

    def validate(self) -> None:
        """验证管线配置的有效性。

        检查项：
            1. 所有 ``next_node_id`` 指向的节点都存在
            2. 节点链中不存在循环引用

        Raises:
            ValueError: 配置无效（引用不存在或存在环）。
        """
        # 检查 next_node_id 引用有效性
        for node in self._nodes:
            if node.next_node_id is not None and node.next_node_id not in self._node_map:
                raise ValueError(
                    f"节点 {node.node_id} 的 next_node_id "
                    f"'{node.next_node_id}' 不存在于节点列表中"
                )

        # 检查环 —— 从起始节点开始遍历
        visited: set[str] = set()
        current_id: str | None = self._nodes[0].node_id

        while current_id is not None:
            if current_id in visited:
                raise ValueError(
                    f"管线中检测到循环引用，节点 {current_id} 被重复访问"
                )
            visited.add(current_id)
            node = self._node_map.get(current_id)
            if node is None:
                break
            current_id = node.next_node_id

    async def execute(self, ctx: IngestionContext) -> IngestionContext:
        """执行管线。

        从第一个节点开始，按 ``next_node_id`` 链式执行。
        处理条件跳过、错误捕获和耗时记录。

        Args:
            ctx: 管线执行上下文。

        Returns:
            执行完成后的上下文（可能状态为 COMPLETED 或 FAILED）。
        """
        ctx.mark_running()
        self._execution_records.clear()

        current_id: str | None = self._nodes[0].node_id

        while current_id is not None:
            config = self._node_map.get(current_id)
            if config is None:
                logger.error("管线执行: 找不到节点 %s", current_id)
                ctx.mark_failed(f"找不到节点配置: {current_id}")
                return ctx

            record = NodeExecutionRecord(
                node_id=config.node_id,
                node_type=config.node_type,
            )

            try:
                # 创建节点实例
                node = get_node(config.node_type)

                # 检查执行条件
                should_run = await node.should_execute(ctx, config.condition_json)
                if not should_run:
                    record.status = "skipped"
                    record.start_time = time.monotonic()
                    record.end_time = record.start_time
                    self._execution_records.append(record)
                    logger.info(
                        "管线执行: 节点 %s (%s) 被跳过",
                        config.node_id,
                        config.node_type,
                    )
                    current_id = config.next_node_id
                    continue

                # 执行节点
                record.start_time = time.monotonic()
                await node.execute(ctx, config.settings_json)
                record.end_time = time.monotonic()
                record.duration_ms = (record.end_time - record.start_time) * 1000.0
                record.status = "ok"

                logger.info(
                    "管线执行: 节点 %s (%s) 完成, 耗时 %.2fms",
                    config.node_id,
                    config.node_type,
                    record.duration_ms,
                )

            except Exception as exc:
                record.end_time = time.monotonic()
                record.duration_ms = (record.end_time - record.start_time) * 1000.0
                record.status = "error"
                record.error = str(exc)

                logger.error(
                    "管线执行: 节点 %s (%s) 执行失败: %s",
                    config.node_id,
                    config.node_type,
                    exc,
                )
                ctx.mark_failed(f"节点 {config.node_id} ({config.node_type}) 执行失败: {exc}")
                self._execution_records.append(record)
                return ctx

            self._execution_records.append(record)
            current_id = config.next_node_id

        ctx.mark_completed()
        logger.info(
            "管线执行完成: 任务ID=%d, 分块数量=%d, 状态=%s",
            ctx.task_id,
            len(ctx.chunks),
            ctx.status,
        )
        return ctx
