"""Celery Worker 入口模块。

由 ``celery -A ragent.common.celery_worker worker`` 启动，
确保 ``get_celery_app()`` 被调用以完成配置和任务注册。
"""

from ragent.common.celery_app import get_celery_app

app = get_celery_app()
