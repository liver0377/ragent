"""pytest 全局配置和 fixtures"""
import sys
from pathlib import Path

import pytest

# 将 src 目录添加到 Python 路径
src_path = str(Path(__file__).parent.parent / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)


@pytest.fixture
def anyio_backend():
    """指定 anyio 后端为 asyncio"""
    return "asyncio"
