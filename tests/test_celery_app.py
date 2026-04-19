"""Tests for ragent.common.celery_app module."""
from unittest.mock import MagicMock, patch

import pytest

from ragent.common.celery_app import (
    INGESTION_CHUNK_QUEUE,
    INGESTION_TASK_QUEUE,
    RAG_FEEDBACK_QUEUE,
    celery_app,
    get_celery_app,
)


# ---------------------------------------------------------------------------
# Queue constants
# ---------------------------------------------------------------------------

class TestQueueConstants:
    def test_ingestion_task_queue(self):
        assert INGESTION_TASK_QUEUE == "ingestion.task"

    def test_ingestion_chunk_queue(self):
        assert INGESTION_CHUNK_QUEUE == "ingestion.chunk"

    def test_rag_feedback_queue(self):
        assert RAG_FEEDBACK_QUEUE == "rag.feedback"


# ---------------------------------------------------------------------------
# celery_app
# ---------------------------------------------------------------------------

class TestCeleryApp:
    def test_celery_app_exists(self):
        from celery import Celery
        assert isinstance(celery_app, Celery)

    def test_celery_app_name(self):
        assert celery_app.main == "ragent"


# ---------------------------------------------------------------------------
# get_celery_app
# ---------------------------------------------------------------------------

class TestGetCeleryApp:
    def test_returns_celery_instance(self):
        from celery import Celery
        app = get_celery_app()
        assert isinstance(app, Celery)

    def test_returns_same_instance(self):
        app1 = get_celery_app()
        app2 = get_celery_app()
        assert app1 is app2

    def test_config_applied(self):
        """After get_celery_app, configuration should be applied."""
        app = get_celery_app()
        conf = app.conf
        assert conf.task_serializer == "json"
        assert conf.result_serializer == "json"
        assert "json" in conf.accept_content
        assert conf.task_track_started is True
        assert conf.task_acks_late is True
        assert conf.worker_prefetch_multiplier == 1
        assert conf.timezone == "Asia/Shanghai"
        assert conf.enable_utc is True
