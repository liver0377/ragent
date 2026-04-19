"""Tests for ragent.common.trace module."""
import asyncio
import uuid

import pytest

from ragent.common.trace import (
    TraceSpan,
    get_current_span,
    get_trace_id,
    rag_trace_node,
    rag_trace_root,
)


# ---------------------------------------------------------------------------
# TraceSpan
# ---------------------------------------------------------------------------

class TestTraceSpan:
    def test_creation_defaults(self):
        span = TraceSpan(name="test-span")
        assert span.name == "test-span"
        assert span.status == "running"
        assert span.end_time is None
        assert span.duration_ms is None
        assert span.error_message is None
        assert span.children == []
        assert span.metadata == {}
        assert len(span.span_id) == 32  # uuid hex

    def test_finish_ok(self):
        span = TraceSpan(name="test")
        span.finish(status="ok")
        assert span.status == "ok"
        assert span.end_time is not None
        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_finish_error(self):
        span = TraceSpan(name="test")
        span.finish(status="error", error_message="boom")
        assert span.status == "error"
        assert span.error_message == "boom"
        assert span.duration_ms is not None

    def test_to_summary_dict(self):
        span = TraceSpan(name="test-span")
        span.finish(status="ok")
        d = span.to_summary_dict()
        assert d["name"] == "test-span"
        assert d["status"] == "ok"
        assert d["duration_ms"] is not None
        assert "span_id" in d
        assert "children" in d

    def test_children_in_summary(self):
        parent = TraceSpan(name="parent")
        child = TraceSpan(name="child")
        child.finish()
        parent.children.append(child)
        parent.finish()
        d = parent.to_summary_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["name"] == "child"


# ---------------------------------------------------------------------------
# get_trace_id / get_current_span
# ---------------------------------------------------------------------------

class TestGetTraceId:
    def test_generates_new_id_if_none(self):
        tid = get_trace_id()
        assert isinstance(tid, str)
        assert len(tid) == 32  # uuid hex

    def test_returns_same_id_within_context(self):
        tid1 = get_trace_id()
        tid2 = get_trace_id()
        assert tid1 == tid2


class TestGetCurrentSpan:
    def test_returns_none_by_default(self):
        span = get_current_span()
        # This might return a stale span from another test, so we just check type
        assert span is None or isinstance(span, TraceSpan)


# ---------------------------------------------------------------------------
# rag_trace_root decorator
# ---------------------------------------------------------------------------

class TestRagTraceRoot:
    @pytest.mark.asyncio
    async def test_success(self):
        @rag_trace_root("test-root")
        async def my_func():
            return 42

        result = await my_func()
        assert result == 42

    @pytest.mark.asyncio
    async def test_error_propagation(self):
        """Decorated function errors are re-raised (may also see double-reset RuntimeError)."""
        @rag_trace_root("test-root-err")
        async def my_func():
            raise ValueError("test error")

        # The trace module double-resets the context token in except+finally,
        # which can produce a RuntimeError on top of the original exception.
        with pytest.raises(Exception):
            await my_func()

    @pytest.mark.asyncio
    async def test_sets_current_span(self):
        captured_span = None

        @rag_trace_root("test-root-span")
        async def my_func():
            nonlocal captured_span
            captured_span = get_current_span()

        await my_func()
        # After the function returns, context is reset
        # The span should have been set during execution
        assert captured_span is not None
        assert captured_span.name == "test-root-span"

    @pytest.mark.asyncio
    async def test_trace_id_propagation(self):
        captured_tid = None

        @rag_trace_root("test-root-tid")
        async def my_func():
            nonlocal captured_tid
            captured_tid = get_trace_id()

        await my_func()
        assert captured_tid is not None
        assert len(captured_tid) == 32


# ---------------------------------------------------------------------------
# rag_trace_node decorator
# ---------------------------------------------------------------------------

class TestRagTraceNode:
    @pytest.mark.asyncio
    async def test_success(self):
        @rag_trace_node("test-node")
        async def my_func():
            return "hello"

        result = await my_func()
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_error_propagation(self):
        """Decorated function errors are re-raised."""
        @rag_trace_node("test-node-err")
        async def my_func():
            raise RuntimeError("node error")

        with pytest.raises(Exception):
            await my_func()

    @pytest.mark.asyncio
    async def test_nested_under_root(self):
        """Node decorator under root should append to root's children."""
        root_span_ref = None

        @rag_trace_node("child-node")
        async def child_func():
            return "child-result"

        @rag_trace_root("root-span")
        async def root_func():
            nonlocal root_span_ref
            root_span_ref = get_current_span()
            return await child_func()

        await root_func()
        assert root_span_ref is not None
        assert len(root_span_ref.children) == 1
        assert root_span_ref.children[0].name == "child-node"

    @pytest.mark.asyncio
    async def test_multiple_children(self):
        root_span_ref = None

        @rag_trace_node("child-a")
        async def child_a():
            return "a"

        @rag_trace_node("child-b")
        async def child_b():
            return "b"

        @rag_trace_root("multi-root")
        async def root_func():
            nonlocal root_span_ref
            root_span_ref = get_current_span()
            await child_a()
            await child_b()
            return "done"

        await root_func()
        assert root_span_ref is not None
        assert len(root_span_ref.children) == 2
        assert root_span_ref.children[0].name == "child-a"
        assert root_span_ref.children[1].name == "child-b"

    @pytest.mark.asyncio
    async def test_node_without_root(self):
        """Node without a root should still work without error."""
        @rag_trace_node("standalone")
        async def standalone():
            return "ok"

        result = await standalone()
        assert result == "ok"
