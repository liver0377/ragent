"""模型配置模块的单元测试。"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from ragent.infra.ai.models import (
    CircuitBreakerConfig,
    ModelCandidate,
    ModelConfig,
    ModelConfigManager,
    StreamConfig,
    TaskType,
)


# ---------------------------------------------------------------------------
# ModelCandidate 测试
# ---------------------------------------------------------------------------

class TestModelCandidate:
    """ModelCandidate 数据模型测试。"""

    def test_defaults(self) -> None:
        """验证所有默认值。"""
        c = ModelCandidate(model_name="glm-4-flash")
        assert c.model_name == "glm-4-flash"
        assert c.provider == ""
        assert c.priority == 0
        assert c.timeout == 30.0
        assert c.max_retries == 2
        assert c.enabled is True

    def test_custom_values(self) -> None:
        """验证自定义值正确赋值。"""
        c = ModelCandidate(
            model_name="openai/gpt-4",
            provider="openai",
            priority=5,
            timeout=60.0,
            max_retries=3,
            enabled=False,
        )
        assert c.provider == "openai"
        assert c.priority == 5
        assert c.timeout == 60.0
        assert c.max_retries == 3
        assert c.enabled is False

    def test_model_name_required(self) -> None:
        """验证 model_name 为必填字段。"""
        with pytest.raises(ValidationError):
            ModelCandidate()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CircuitBreakerConfig 测试
# ---------------------------------------------------------------------------

class TestCircuitBreakerConfig:
    """CircuitBreakerConfig 数据模型测试。"""

    def test_defaults(self) -> None:
        """验证默认值。"""
        cb = CircuitBreakerConfig()
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 60
        assert cb.success_threshold == 3

    def test_custom_values(self) -> None:
        """验证自定义值。"""
        cb = CircuitBreakerConfig(
            failure_threshold=10,
            recovery_timeout=120,
            success_threshold=5,
        )
        assert cb.failure_threshold == 10
        assert cb.recovery_timeout == 120
        assert cb.success_threshold == 5


# ---------------------------------------------------------------------------
# StreamConfig 测试
# ---------------------------------------------------------------------------

class TestStreamConfig:
    """StreamConfig 数据模型测试。"""

    def test_defaults(self) -> None:
        """验证默认值。"""
        sc = StreamConfig()
        assert sc.first_packet_timeout == 60.0


# ---------------------------------------------------------------------------
# ModelConfig 测试
# ---------------------------------------------------------------------------

class TestModelConfig:
    """ModelConfig 数据模型测试。"""

    def test_defaults(self) -> None:
        """验证所有列表默认为空。"""
        cfg = ModelConfig()
        assert cfg.chat_models == []
        assert cfg.embedding_models == []
        assert cfg.rerank_models == []
        assert isinstance(cfg.circuit_breaker, CircuitBreakerConfig)
        assert isinstance(cfg.stream, StreamConfig)

    def test_with_candidates(self) -> None:
        """验证正确承载候选列表。"""
        cfg = ModelConfig(
            chat_models=[ModelCandidate(model_name="m1", priority=1)],
            embedding_models=[ModelCandidate(model_name="m2", priority=0)],
        )
        assert len(cfg.chat_models) == 1
        assert cfg.chat_models[0].model_name == "m1"
        assert len(cfg.embedding_models) == 1


# ---------------------------------------------------------------------------
# TaskType 测试
# ---------------------------------------------------------------------------

class TestTaskType:
    """TaskType 枚举测试。"""

    def test_values(self) -> None:
        """验证枚举值。"""
        assert TaskType.CHAT.value == "chat"
        assert TaskType.EMBEDDING.value == "embedding"
        assert TaskType.RERANK.value == "rerank"


# ---------------------------------------------------------------------------
# ModelConfigManager 测试
# ---------------------------------------------------------------------------

class TestModelConfigManager:
    """ModelConfigManager 配置管理器测试。"""

    def test_default_config_uses_settings(self) -> None:
        """验证默认配置从环境变量构建。"""
        with patch("ragent.infra.ai.models.get_settings") as mock_settings:
            mock_settings.return_value.GLM_MODEL = "glm-4-flash"
            mock_settings.return_value.EMBEDDING_MODEL = "embedding-3"

            mgr = ModelConfigManager()
            chat = mgr.get_candidates("chat")
            assert len(chat) == 1
            assert chat[0].model_name == "glm-4-flash"
            assert chat[0].provider == "zhipu"

            embed = mgr.get_candidates("embedding")
            assert len(embed) == 1
            assert embed[0].model_name == "embedding-3"

            rerank = mgr.get_candidates("rerank")
            assert rerank == []

    def test_explicit_config(self) -> None:
        """验证传入显式配置。"""
        cfg = ModelConfig(
            chat_models=[
                ModelCandidate(model_name="custom-model", priority=10),
            ],
        )
        mgr = ModelConfigManager(config=cfg)
        chat = mgr.get_candidates("chat")
        assert len(chat) == 1
        assert chat[0].model_name == "custom-model"

    def test_get_candidates_unknown_type(self) -> None:
        """验证未知任务类型抛出 ValueError。"""
        mgr = ModelConfigManager(config=ModelConfig())
        with pytest.raises(ValueError, match="未知的任务类型"):
            mgr.get_candidates("unknown")

    def test_from_yaml(self, tmp_path: Path) -> None:
        """验证从 YAML 文件加载配置。"""
        yaml_content = textwrap.dedent("""\
            chat_models:
              - model_name: yaml-chat-model
                provider: test
                priority: 3
            embedding_models:
              - model_name: yaml-embed-model
                priority: 0
            circuit_breaker:
              failure_threshold: 10
              recovery_timeout: 120
        """)
        yaml_file = tmp_path / "models.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        mgr = ModelConfigManager.from_yaml(yaml_file)
        chat = mgr.get_candidates("chat")
        assert len(chat) == 1
        assert chat[0].model_name == "yaml-chat-model"
        assert chat[0].provider == "test"
        assert chat[0].priority == 3

        embed = mgr.get_candidates("embedding")
        assert len(embed) == 1
        assert embed[0].model_name == "yaml-embed-model"

        # 验证熔断器配置被正确加载
        assert mgr.config.circuit_breaker.failure_threshold == 10
        assert mgr.config.circuit_breaker.recovery_timeout == 120
        # success_threshold 未指定，应使用默认值
        assert mgr.config.circuit_breaker.success_threshold == 3

    def test_from_yaml_file_not_found(self) -> None:
        """验证文件不存在时抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="模型配置文件不存在"):
            ModelConfigManager.from_yaml("/nonexistent/path.yaml")

    def test_from_yaml_empty_file(self, tmp_path: Path) -> None:
        """验证空 YAML 文件使用全部默认值。"""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("", encoding="utf-8")

        mgr = ModelConfigManager.from_yaml(yaml_file)
        assert mgr.config.chat_models == []
        assert mgr.config.circuit_breaker.failure_threshold == 5

    def test_config_property(self) -> None:
        """验证 config 属性返回配置实例。"""
        cfg = ModelConfig()
        mgr = ModelConfigManager(config=cfg)
        assert mgr.config is cfg

    def test_get_candidates_returns_copy(self) -> None:
        """验证 get_candidates 返回列表副本，不影响原始数据。"""
        cfg = ModelConfig(
            chat_models=[ModelCandidate(model_name="m1")],
        )
        mgr = ModelConfigManager(config=cfg)
        candidates = mgr.get_candidates("chat")
        candidates.clear()
        # 原始列表不应被修改
        assert len(mgr.get_candidates("chat")) == 1
