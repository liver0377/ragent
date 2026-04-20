"""
Locust 压测脚本 — RAG Agent 服务
===================================
三种用户类型：
  1. HealthCheckUser — 快速健康检查 (1 req/s)
  2. ChatUser        — RAG 问答用户 (0.2-0.5 req/s, SSE 流式)
  3. MixedUser       — 混合用户 (80% health / 20% chat)

用法:
  locust -f locustfile.py --host http://localhost:8000
  locust -f locustfile.py --host http://localhost:8000 --headless -u 50 -r 10 -t 60s
"""

import json
import time
import itertools
from locust import HttpUser, task, between, constant, events
from locust.runners import MasterRunner, WorkerRunner


# ── 预定义 RAG 问题池 ────────────────────────────────────────────────
RAG_QUESTIONS = [
    "什么是向量数据库？它和传统数据库有什么区别？",
    "RAG 技术的核心原理是什么？",
    "如何评估一个检索增强生成系统的效果？",
    "Embedding 模型的选择对检索质量有什么影响？",
    "请解释余弦相似度和欧氏距离的区别",
    "什么是语义搜索？它与传统关键词搜索有何不同？",
    "LangChain 框架中如何构建 RAG pipeline？",
    "文档分块（chunking）的最佳实践是什么？",
    "如何处理 RAG 系统中的幻觉（hallucination）问题？",
    "多轮对话场景下如何维护上下文信息？",
]


# ── 1. HealthCheckUser ──────────────────────────────────────────────
class HealthCheckUser(HttpUser):
    """快速健康检查用户 — 恒定 1 req/s"""

    weight = 5  # 默认权重比例
    wait_time = constant(1)

    @task
    def health_check(self):
        self.client.get("/api/v1/health", name="/api/v1/health")


# ── 2. ChatUser ─────────────────────────────────────────────────────
class ChatUser(HttpUser):
    """RAG 问答用户 — 模拟真实用户，0.2~0.5 req/s，SSE 流式响应"""

    weight = 2
    wait_time = between(2, 5)  # 2-5s 间隔 → 0.2-0.5 req/s

    def on_start(self):
        """每个用户实例启动时，初始化一个问题轮询迭代器"""
        self._question_cycle = itertools.cycle(RAG_QUESTIONS)

    @task
    def chat(self):
        question = next(self._question_cycle)
        payload = {
            "question": question,
            "conversation_id": None,
            "user_id": None,
        }
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}

        start = time.monotonic()

        with self.client.post(
            "/api/v1/chat",
            json=payload,
            headers=headers,
            name="/api/v1/chat",
            stream=True,
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return

            # 消费 SSE 流，记录完整响应时间
            full_text = []
            try:
                for line in resp.iter_lines():
                    if line:
                        decoded = line.decode("utf-8", errors="replace")
                        if decoded.startswith("data:"):
                            data_str = decoded[len("data:"):].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                content = chunk.get("content", "")
                                if content:
                                    full_text.append(content)
                            except json.JSONDecodeError:
                                pass
            except Exception as exc:
                resp.failure(f"SSE stream error: {exc}")
                return

            elapsed = time.monotonic() - start
            # 利用 meta 将完整流式耗时附加到 Locust 统计
            resp.success()
            # 手动微调 request_meta 以反映真实流式结束时间
            # Locust 默认记录的是 headers 收到时间，这里我们追加一个事件
            events.request.fire(
                request_type="SSE-FULL",
                name="/api/v1/chat [stream complete]",
                response_time=elapsed * 1000,
                response_length=len("".join(full_text)),
                exception=None,
            )


# ── 3. MixedUser ────────────────────────────────────────────────────
class MixedUser(HttpUser):
    """混合用户 — 80% 健康检查, 20% RAG 问答"""

    weight = 3
    wait_time = between(1, 3)

    def on_start(self):
        self._question_cycle = itertools.cycle(RAG_QUESTIONS)

    @task(4)  # 80%
    def health_check(self):
        self.client.get("/api/v1/health", name="/api/v1/health")

    @task(1)  # 20%
    def chat(self):
        question = next(self._question_cycle)
        payload = {
            "question": question,
            "conversation_id": None,
            "user_id": None,
        }
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}

        start = time.monotonic()

        with self.client.post(
            "/api/v1/chat",
            json=payload,
            headers=headers,
            name="/api/v1/chat",
            stream=True,
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return

            full_text = []
            try:
                for line in resp.iter_lines():
                    if line:
                        decoded = line.decode("utf-8", errors="replace")
                        if decoded.startswith("data:"):
                            data_str = decoded[len("data:"):].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                content = chunk.get("content", "")
                                if content:
                                    full_text.append(content)
                            except json.JSONDecodeError:
                                pass
            except Exception as exc:
                resp.failure(f"SSE stream error: {exc}")
                return

            elapsed = time.monotonic() - start
            resp.success()
            events.request.fire(
                request_type="SSE-FULL",
                name="/api/v1/chat [stream complete]",
                response_time=elapsed * 1000,
                response_length=len("".join(full_text)),
                exception=None,
            )
