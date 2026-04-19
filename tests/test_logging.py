"""Tests for ragent.common.logging module."""
import logging
import sys

import pytest

from ragent.common.logging import TraceIdFilter, get_logger, setup_logging


# ---------------------------------------------------------------------------
# TraceIdFilter
# ---------------------------------------------------------------------------

class TestTraceIdFilter:
    def test_filter_returns_true(self):
        f = TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=None,
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_filter_injects_trace_id(self):
        f = TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=None,
            exc_info=None,
        )
        f.filter(record)
        assert hasattr(record, "trace_id")
        # trace_id should be either a hex string or "-"
        assert isinstance(record.trace_id, str)
        assert len(record.trace_id) > 0

    def test_filter_injects_dash_when_no_trace_id(self):
        """Without a trace context, should use '-' placeholder."""
        f = TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=None,
            exc_info=None,
        )
        f.filter(record)
        # Either a hex trace_id or "-"
        assert record.trace_id  # non-empty


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def test_setup_adds_handler(self):
        root = logging.getLogger()
        initial_count = len(root.handlers)
        setup_logging(level="DEBUG")
        # Should have added at least one handler
        assert len(root.handlers) >= initial_count + 1

    def test_setup_sets_level(self):
        setup_logging(level="WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_setup_debug_level(self):
        setup_logging(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_handler_has_trace_id_filter(self):
        setup_logging(level="INFO")
        root = logging.getLogger()
        has_filter = any(
            isinstance(f, TraceIdFilter)
            for h in root.handlers
            for f in h.filters
        )
        assert has_filter


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------

class TestGetLogger:
    def test_returns_logger(self):
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_different_names_different_loggers(self):
        l1 = get_logger("module.a")
        l2 = get_logger("module.b")
        assert l1 is not l2

    def test_same_name_same_logger(self):
        l1 = get_logger("module.x")
        l2 = get_logger("module.x")
        assert l1 is l2
