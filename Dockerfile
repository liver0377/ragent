FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（pgvector 编译需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存层
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 复制源码
COPY src/ src/

# 默认启动命令
CMD ["uvicorn", "ragent.main:app", "--host", "0.0.0.0", "--port", "8000"]
