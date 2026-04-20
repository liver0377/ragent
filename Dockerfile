# ===========================================================================
# Ragent Dockerfile — 多阶段构建
# 阶段1: builder — 安装依赖
# 阶段2: runtime — 精简运行时镜像
# ===========================================================================

# ---------- 阶段1: 构建依赖 ----------
FROM python:3.11-slim AS builder

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /build

# 先复制依赖定义文件，利用 Docker 缓存层
COPY pyproject.toml uv.lock ./

# 创建 src 目录占位，让 setuptools 能发现包
RUN mkdir -p src/ragent && touch src/ragent/__init__.py

# uv sync 安装依赖到 .venv（使用最终路径以避免 shebang 问题）
RUN uv venv /app/.venv && \
    UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --frozen --no-dev --no-install-project

# 复制源码并安装项目本身
COPY src/ragent/ ./src/ragent/
RUN UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --frozen --no-dev

# ---------- 阶段2: 运行时 ----------
FROM python:3.11-slim AS runtime

LABEL maintainer="ragent-bot <bot@ragent.dev>"
LABEL description="Ragent - RAG intelligent agent platform"

WORKDIR /app

# 安装运行时必需的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 复制安装好的虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 复制源码（保持 src 布局）
COPY src/ragent/ ./src/ragent/
COPY deploy/ ./deploy/

# 确保 venv 在 PATH 中
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# 让 Python 能找到 src 下的包
ENV PYTHONPATH="/app/src:$PYTHONPATH"

# 暴露 FastAPI 端口
EXPOSE 8000

# 默认启动 FastAPI 服务
CMD ["uvicorn", "ragent.main:app", "--host", "0.0.0.0", "--port", "8000"]
