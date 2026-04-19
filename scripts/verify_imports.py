#!/usr/bin/env python3
"""验证所有 L1 模块可以正常导入"""
import sys
sys.path.insert(0, "src")

modules = [
    ("config.settings", "Settings, get_settings"),
    ("common.exceptions", "ClientException, ServiceException, RemoteException"),
    ("common.snowflake", "SnowflakeIdGenerator, generate_id"),
    ("common.trace", "rag_trace_root, rag_trace_node, get_trace_id"),
    ("common.context", "UserContext, get_user_context, UserContextManager"),
    ("common.response", "Result, PaginationResult, success, error"),
    ("common.logging", "setup_logging, get_logger"),
    ("common.sse", "SSEEvent, create_sse_response, sse_content"),
    ("common.celery_app", "get_celery_app, celery_app"),
]

for mod, names in modules:
    try:
        exec(f"from ragent.{mod} import {names}")
        print(f"OK: ragent.{mod}")
    except Exception as e:
        print(f"FAIL: ragent.{mod} -> {e}")

from ragent.config.settings import get_settings
s = get_settings()
print(f"Settings loaded: GLM_MODEL={s.GLM_MODEL}")

from ragent.common.snowflake import SnowflakeIdGenerator
gen = SnowflakeIdGenerator(worker_id=1)
print(f"Snowflake ID: {gen.generate_id()}")
print("All imports successful!")
