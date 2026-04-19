"""Tests for ragent.common.sse module."""
import json

import pytest

from ragent.common.sse import (
    SSEEvent,
    SSEEventType,
    create_sse_response,
    sse_content,
    sse_error,
    sse_finish,
    sse_generator,
    sse_meta,
    sse_thinking,
)


# ---------------------------------------------------------------------------
# SSEEventType
# ---------------------------------------------------------------------------

class TestSSEEventType:
    def test_constants(self):
        assert SSEEventType.META == "meta"
        assert SSEEventType.THINKING == "thinking"
        assert SSEEventType.CONTENT == "content"
        assert SSEEventType.ERROR == "error"
        assert SSEEventType.FINISH == "finish"


# ---------------------------------------------------------------------------
# SSEEvent
# ---------------------------------------------------------------------------

class TestSSEEvent:
    def test_creation(self):
        evt = SSEEvent(event="test", data='{"key": "value"}')
        assert evt.event == "test"
        assert evt.data == '{"key": "value"}'
        assert evt.id is None
        assert evt.retry is None

    def test_creation_with_optional_fields(self):
        evt = SSEEvent(event="test", data="{}", id="123", retry=5000)
        assert evt.id == "123"
        assert evt.retry == 5000

    def test_frozen(self):
        evt = SSEEvent(event="test", data="{}")
        with pytest.raises(Exception):
            evt.event = "changed"  # type: ignore


# ---------------------------------------------------------------------------
# sse_generator
# ---------------------------------------------------------------------------

class TestSseGenerator:
    @pytest.mark.asyncio
    async def test_basic_event(self):
        async def event_source():
            yield SSEEvent(event="content", data='{"text": "hello"}')

        chunks = []
        async for chunk in sse_generator(event_source()):
            chunks.append(chunk)

        assert len(chunks) == 1
        output = chunks[0]
        assert "event: content" in output
        assert 'data: {"text": "hello"}' in output

    @pytest.mark.asyncio
    async def test_event_with_id_and_retry(self):
        async def event_source():
            yield SSEEvent(event="test", data="{}", id="42", retry=3000)

        chunks = []
        async for chunk in sse_generator(event_source()):
            chunks.append(chunk)

        output = chunks[0]
        assert "id: 42" in output
        assert "retry: 3000" in output

    @pytest.mark.asyncio
    async def test_multiple_events(self):
        async def event_source():
            yield SSEEvent(event="meta", data='{"model":"gpt"}')
            yield SSEEvent(event="content", data='{"content":"hi"}')
            yield SSEEvent(event="finish", data="{}")

        chunks = []
        async for chunk in sse_generator(event_source()):
            chunks.append(chunk)

        assert len(chunks) == 3
        # Each chunk ends with double newline (empty lines)
        for chunk in chunks:
            assert chunk.endswith("\n\n")

    @pytest.mark.asyncio
    async def test_no_id_no_retry(self):
        async def event_source():
            yield SSEEvent(event="test", data="{}")

        chunks = []
        async for chunk in sse_generator(event_source()):
            chunks.append(chunk)

        output = chunks[0]
        assert "id:" not in output
        assert "retry:" not in output


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

class TestSseMeta:
    def test_creates_meta_event(self):
        evt = sse_meta({"model": "glm-4"})
        assert evt.event == "meta"
        data = json.loads(evt.data)
        assert data == {"model": "glm-4"}


class TestSseThinking:
    def test_creates_thinking_event(self):
        evt = sse_thinking("I am thinking about...")
        assert evt.event == "thinking"
        data = json.loads(evt.data)
        assert data["content"] == "I am thinking about..."


class TestSseContent:
    def test_creates_content_event(self):
        evt = sse_content("Hello world")
        assert evt.event == "content"
        data = json.loads(evt.data)
        assert data["content"] == "Hello world"


class TestSseError:
    def test_error_without_code(self):
        evt = sse_error("Something went wrong")
        assert evt.event == "error"
        data = json.loads(evt.data)
        assert data["message"] == "Something went wrong"
        assert "code" not in data

    def test_error_with_code(self):
        evt = sse_error("Failed", code="E001")
        data = json.loads(evt.data)
        assert data["message"] == "Failed"
        assert data["code"] == "E001"


class TestSseFinish:
    def test_finish_with_no_data(self):
        evt = sse_finish()
        assert evt.event == "finish"
        data = json.loads(evt.data)
        assert data == {}

    def test_finish_with_data(self):
        evt = sse_finish({"total_tokens": 100})
        data = json.loads(evt.data)
        assert data == {"total_tokens": 100}


# ---------------------------------------------------------------------------
# create_sse_response
# ---------------------------------------------------------------------------

class TestCreateSseResponse:
    def test_returns_streaming_response(self):
        from starlette.responses import StreamingResponse

        async def event_source():
            yield SSEEvent(event="test", data="{}")

        resp = create_sse_response(event_source())
        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"

    def test_response_headers(self):
        async def event_source():
            yield SSEEvent(event="test", data="{}")

        resp = create_sse_response(event_source())
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("connection") == "keep-alive"
        assert resp.headers.get("x-accel-buffering") == "no"
