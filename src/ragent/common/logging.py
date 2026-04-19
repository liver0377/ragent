"""
结构化日志模块 —— 基于 trace_id 的日志增强

提供以下功能：
    - ``TraceIdFilter`` —— 日志过滤器，自动注入 trace_id 到每条日志记录
    - ``setup_logging()`` —— 配置根日志记录器的结构化格式
    - ``get_logger()`` —— 获取命名日志记录器的便捷函数

格式示例::

    2025-01-01 12:00:00,000 | INFO     | [abc123def456] | ragent.rag | 处理查询完成

设计要点：
    - trace_id 通过 ``ragent.common.trace.get_trace_id()`` 获取
    - 若当前无 trace_id，则使用 ``'-'`` 占位
    - 输出目标为 ``sys.stdout``，便于容器化环境收集日志
"""

from __future__ import annotations

import logging
import sys

from ragent.common.trace import get_trace_id


# ---------------------------------------------------------------------------
# 日志过滤器
# ---------------------------------------------------------------------------


class TraceIdFilter(logging.Filter):
    """日志过滤器，将 trace_id 注入到每条日志记录中。

    该过滤器会从 ``ragent.common.trace.get_trace_id()`` 获取当前上下文的
    追踪标识，并将其设置为日志记录的 ``trace_id`` 属性。

    若当前上下文中不存在 trace_id，则使用 ``'-'`` 作为占位符。

    Example::

        handler = logging.StreamHandler(sys.stdout)
        handler.addFilter(TraceIdFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """为日志记录注入 trace_id 属性。

        Args:
            record: 待处理的日志记录对象。

        Returns:
            始终返回 ``True``，表示所有日志记录都应通过过滤。
        """
        try:
            trace_id: str = get_trace_id()
        except Exception:
            trace_id = ""

        record.trace_id = trace_id if trace_id else "-"  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------

# 结构化日志格式：时间 | 级别 | [trace_id] | 模块名 | 消息
_LOG_FORMAT: str = (
    "%(asctime)s | %(levelname)-8s | [%(trace_id)s] | %(name)s | %(message)s"
)


def setup_logging(level: str = "INFO") -> None:
    """配置根日志记录器，使用结构化格式输出日志。

    该函数会：
        1. 创建 ``StreamHandler`` 输出到 ``sys.stdout``
        2. 添加 ``TraceIdFilter`` 以自动注入 trace_id
        3. 设置结构化日志格式
        4. 将日志级别设置为指定级别
        5. 配置根日志记录器

    注意：该函数应在应用启动时调用一次。重复调用会添加额外的 Handler，
    但通常不会造成问题，因为 ``logging`` 内部会去重。

    Args:
        level: 日志级别字符串，默认 ``'INFO'``。
               支持 ``'DEBUG'``、``'INFO'``、``'WARNING'``、``'ERROR'``、``'CRITICAL'``。

    Example::

        from ragent.common.logging import setup_logging

        setup_logging('DEBUG')
    """
    # 创建输出到 stdout 的处理器
    handler: logging.StreamHandler[sys.stdout] = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceIdFilter())

    # 设置格式化器
    formatter = logging.Formatter(_LOG_FORMAT)
    handler.setFormatter(formatter)

    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.addHandler(handler)


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的日志记录器。

    这是 ``logging.getLogger()`` 的便捷封装，用于在模块中快速获取日志记录器。

    Args:
        name: 日志记录器名称，通常使用 ``__name__`` 传入。

    Returns:
        指定名称的 ``logging.Logger`` 实例。

    Example::

        from ragent.common.logging import get_logger

        logger = get_logger(__name__)
        logger.info("处理完成")
    """
    return logging.getLogger(name)
