"""Tests for ragent.config.settings module."""
import os
from unittest.mock import patch

import pytest

from ragent.config.settings import Settings, get_settings


class TestSettings:
    """Test the Settings pydantic model."""

    def test_default_values(self):
        """All fields have sensible defaults so Settings() works without .env."""
        s = Settings()
        # GLM_API_KEY may be loaded from .env, just check it's a string
        assert isinstance(s.GLM_API_KEY, str)
        assert s.GLM_BASE_URL == "https://open.bigmodel.cn/api/coding/paas/v4"
        assert s.GLM_MODEL == "glm-4-flash"
        assert s.EMBEDDING_MODEL == "embedding-3"
        assert s.REDIS_URL == "redis://localhost:6379/0"
        assert s.MILVUS_HOST == "localhost"
        assert s.MILVUS_PORT == 19530
        assert s.CELERY_BROKER_URL == "redis://localhost:6379/1"
        assert s.CELERY_RESULT_BACKEND == "redis://localhost:6379/2"
        assert s.APP_NAME == "ragent"
        assert s.APP_VERSION == "0.1.0"
        assert s.DEBUG is False
        assert s.LOG_LEVEL == "INFO"
        assert s.API_PREFIX == "/api/v1"
        assert s.LLM_TIMEOUT == 60
        assert s.LLM_MAX_RETRIES == 3
        assert s.EMBEDDING_DIMENSION == 2048
        assert s.CHUNK_SIZE == 512
        assert s.CHUNK_OVERLAP == 64
        assert s.RETRIEVAL_TOP_K == 5
        assert s.RATE_LIMIT_MAX_CONCURRENT == 10
        assert s.RATE_LIMIT_WINDOW_SECONDS == 60
        assert s.SESSION_MAX_ROUNDS == 10
        assert s.SESSION_SUMMARY_THRESHOLD == 6

    def test_env_override(self):
        """Environment variables override defaults."""
        with patch.dict(os.environ, {"GLM_MODEL": "glm-5", "DEBUG": "true"}):
            s = Settings()
            assert s.GLM_MODEL == "glm-5"
            assert s.DEBUG is True

    def test_extra_env_vars_ignored(self):
        """Extra env vars are silently ignored (extra='ignore')."""
        with patch.dict(os.environ, {"UNKNOWN_VAR_XYZ": "should_be_ignored"}):
            s = Settings()  # should not raise
            assert s.APP_NAME == "ragent"

    def test_model_config_case_sensitive(self):
        """model_config sets case_sensitive=True."""
        assert Settings.model_config["case_sensitive"] is True

    def test_case_sensitive_no_lowercase_override(self):
        """Lowercase env var should NOT override when case_sensitive=True."""
        with patch.dict(os.environ, {"glm_model": "should-not-override"}):
            s = Settings()
            # Still uses default because env var is lowercase
            assert s.GLM_MODEL == "glm-4-flash"


class TestGetSettings:
    """Test the get_settings() singleton function."""

    def test_singleton_returns_same_instance(self):
        """Repeated calls return the exact same object."""
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_returns_settings_instance(self):
        """Return type is Settings."""
        s = get_settings()
        assert isinstance(s, Settings)
