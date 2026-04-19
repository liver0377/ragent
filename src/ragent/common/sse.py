"""
SSE（Server-Sent Events）流式输出模块 —— FastAPI 流式响应封装

提供以下功能：
    - ``SSEEvent`` —— SSE 事件数据类
    - ``SSEEventType`` —— SSE 事件类型常量
    - ``sse_generator()`` —— 异步生成器，将 SSEEvent 转换为 SSE 协议格式
    - ``create_sse_response()`` —— 创建 FastAPI StreamingResponse
    - 便捷工厂函数：``sse_meta()``、``sse_thinking()``、``sse_content()``、``sse_error()``、``sse_finish()``

SSE 协议格式::

    event: {事件类型}
    data: {JSON 载荷}

    id: {可选事件ID}
    retry: {可选重连间隔(ms)}

设计要点：
    - 事件类型固定为五种：meta、thinking、content、error、finish
    - data 字段始终为 JSON 字符串
    - 通过异步生成器自然处理背压（backpressure）
    - 响应头设置 ``Cache-Control: no-cache`` 防止缓存
    - 响应头设置 ``X-Accel-Buffering: no`` 禁用 Nginx 缓冲
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

from starlette.responses import StreamingResponse


# ---------------------------------------------------------------------------
# SSE 事件类型常量
# ---------------------------------------------------------------------------


class SSEEventType:
    """SSE 事件类型常量集合。

    定义了所有支持的 SSE 事件类型：
        - ``META``     元数据事件，用于在流开始时传递额外信息
        - ``THINKING`` 思考过程事件，用于展示 AI 的推理过程
        - ``CONTENT``  内容事件，用于传递实际的生成内容
        - ``ERROR``    错误事件，用于传递错误信息
        - ``FINISH``   结束事件，用于标记流的结束
    """

    META: str = "meta"
    THINKING: str = "thinking"
    CONTENT: str = "content"
    ERROR: str = "error"
    FINISH: str = "finish"


# ---------------------------------------------------------------------------
# SSE 事件数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SSEEvent:
    """SSE 事件数据类。

    表示一个 Server-Sent Event，包含事件类型、数据载荷以及可选的
    事件 ID 和重连间隔。

    Attributes:
        event: 事件类型，应为 ``SSEEventType`` 中定义的常量之一。
        data:  事件载荷，JSON 字符串格式。
        id:    可选的事件标识符，用于客户端断线重连时的最后事件定位。
        retry: 可选的重连间隔（毫秒），指示客户端在连接断开后应等待多久重连。
    """

    event: str
    data: str
    id: str | None = None
    retry: int | None = None


# ---------------------------------------------------------------------------
# SSE 异步生成器
# ---------------------------------------------------------------------------


async def sse_generator(
    events: AsyncIterator[SSEEvent],
) -> AsyncGenerator[str, None]:
    """将 SSEEvent 异步迭代器转换为 SSE 协议格式的异步生成器。

    遍历输入的 SSEEvent 对象，将其转换为符合 SSE 规范的文本格式。
    每个事件之间以空行分隔，自然支持异步背压。

    输出格式::

        event: {event}
        data: {data}
        id: {id}          （仅当 id 不为 None 时包含）
        retry: {retry}    （仅当 retry 不为 None 时包含）

        （空行）

    Args:
        events: SSEEvent 异步迭代器。

    Yields:
        符合 SSE 协议格式的字符串。
    """
    async for evt in events:
        lines: list[str] = [f"event: {evt.event}", f"data: {evt.data}"]

        if evt.id is not None:
            lines.append(f"id: {evt.id}")
        if evt.retry is not None:
            lines.append(f"retry: {evt.retry}")

        # SSE 协议要求每个事件以空行结尾
        lines.append("")
        lines.append("")

        yield "\n".join(lines)


# ---------------------------------------------------------------------------
# FastAPI 响应创建
# ---------------------------------------------------------------------------


def create_sse_response(
    events: AsyncIterator[SSEEvent],
) -> StreamingResponse:
    """创建 FastAPI StreamingResponse 以 SSE 格式输出流式事件。

    该函数封装了 ``StreamingResponse`` 的创建过程，自动设置
    SSE 所需的响应头和媒体类型。

    响应头说明：
        - ``Content-Type: text/event-stream`` —— SSE 标准媒体类型
        - ``Cache-Control: no-cache`` —— 禁止客户端和中间代理缓存
        - ``Connection: keep-alive`` —— 保持长连接
        - ``X-Accel-Buffering: no`` —— 禁用 Nginx 反向代理的缓冲

    Args:
        events: SSEEvent 异步迭代器。

    Returns:
        配置好 SSE 响应头和生成器的 ``StreamingResponse`` 实例。

    Example::

        async def stream_events(query: str) -> AsyncIterator[SSEEvent]:
            yield sse_meta({"model": "gpt-4"})
            yield sse_content("你好，世界")
            yield sse_finish()

        @app.post("/chat")
        async def chat(request: ChatRequest):
            return create_sse_response(stream_events(request.query))
    """
    return StreamingResponse(
        content=sse_generator(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 便捷工厂函数
# ---------------------------------------------------------------------------


def sse_meta(data: dict) -> SSEEvent:
    """创建元数据事件。

    用于在流开始时传递元信息，例如模型名称、请求参数等。

    Args:
        data: 元数据字典，将被序列化为 JSON 字符串。

    Returns:
        事件类型为 ``meta`` 的 ``SSEEvent`` 实例。
    """
    return SSEEvent(event=SSEEventType.META, data=json.dumps(data, ensure_ascii=False))


def sse_thinking(content: str) -> SSEEvent:
    """创建思考过程事件。

    用于展示 AI 的推理或思考过程，例如思维链（Chain-of-Thought）的中间步骤。

    Args:
        content: 思考内容文本。

    Returns:
        事件类型为 ``thinking`` 的 ``SSEEvent`` 实例。
    """
    return SSEEvent(
        event=SSEEventType.THINKING,
        data=json.dumps({"content": content}, ensure_ascii=False),
    )


def sse_content(content: str) -> SSEEvent:
    """创建内容事件。

    用于传递实际的生成内容片段，客户端应拼接所有 content 事件以获得完整输出。

    Args:
        content: 内容文本片段。

    Returns:
        事件类型为 ``content`` 的 ``SSEEvent`` 实例。
    """
    return SSEEvent(
        event=SSEEventType.CONTENT,
        data=json.dumps({"content": content}, ensure_ascii=False),
    )


def sse_error(message: str, code: str = "") -> SSEEvent:
    """创建错误事件。

    用于在流处理过程中传递错误信息。客户端收到此事件后应终止当前流的处理。

    Args:
        message: 错误描述信息。
        code:    错误代码（可选），用于程序化的错误分类和处理。

    Returns:
        事件类型为 ``error`` 的 ``SSEEvent`` 实例。
    """
    payload: dict[str, str] = {"message": message}
    if code:
        payload["code"] = code
    return SSEEvent(
        event=SSEEventType.ERROR,
        data=json.dumps(payload, ensure_ascii=False),
    )


def sse_finish(data: dict | None = None) -> SSEEvent:
    """创建结束事件。

    用于标记 SSE 流的结束，客户端收到此事件后应关闭连接。
    可选地附带额外的结束数据，例如统计数据或总结信息。

    Args:
        data: 可选的结束数据字典，若为 ``None`` 则传递空字典。

    Returns:
        事件类型为 ``finish`` 的 ``SSEEvent`` 实例。
    """
    return SSEEvent(
        event=SSEEventType.FINISH,
        data=json.dumps(data or {}, ensure_ascii=False),
    )
