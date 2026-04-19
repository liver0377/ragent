"""
ragent.common —— 通用工具与基础设施
"""

from ragent.common.exceptions import (
    BaseError,
    ClientException,
    RemoteException,
    ServiceException,
    raise_client_error,
    raise_remote_error,
    raise_service_error,
)
from ragent.common.trace import (
    TraceSpan,
    get_current_span,
    get_trace_id,
    rag_trace_node,
    rag_trace_root,
)

__all__ = [
    "BaseError",
    "ClientException",
    "ServiceException",
    "RemoteException",
    "raise_client_error",
    "raise_service_error",
    "raise_remote_error",
    "TraceSpan",
    "get_current_span",
    "get_trace_id",
    "rag_trace_node",
    "rag_trace_root",
]
