# ===========================================================================
# Gunicorn 配置 — Ragent FastAPI 生产部署
# ===========================================================================
import multiprocessing

# 监听
bind = "0.0.0.0:8000"

# Worker 配置
worker_class = "uvicorn.workers.UvicornWorker"
workers = 4  # 推荐 2-4 × CPU核心数

# 超时
timeout = 120
graceful_timeout = 30
keepalive = 5

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 性能调优
preload_app = True
max_requests = 5000       # 内存泄漏防护：每个 worker 处理 5000 请求后重启
max_requests_jitter = 500  # 随机抖动，避免同时重启
