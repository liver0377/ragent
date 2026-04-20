#!/usr/bin/env python3
"""
simple_stress.py — 轻量级 Python 压测脚本（不依赖 Locust）
══════════════════════════════════════════════════════════════════════
用 httpx + asyncio 实现并发请求压测:
  1. Health 接口并发测试（100 并发, 1000 请求）
  2. Chat 接口并发测试（10 并发, 50 请求, SSE 流式）
  3. 输出统计: 总耗时、平均响应时间、P50/P95/P99、成功率、吞吐量

用法:
  python scripts/simple_stress.py [--host http://localhost:8000]
  python scripts/simple_stress.py --host http://your-server:8000 --health-concurrency 200 --health-requests 2000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field

try:
    import httpx
except ImportError:
    print("❌ 需要安装 httpx:  pip install httpx  或  uv add httpx")
    sys.exit(1)


# ── 常量 ────────────────────────────────────────────────────────────

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


# ── 数据结构 ────────────────────────────────────────────────────────

@dataclass
class RequestResult:
    """单次请求结果"""
    status_code: int
    elapsed_ms: float       # 响应时间 (ms)
    success: bool
    error: str | None = None
    response_length: int = 0


@dataclass
class Stats:
    """统计汇总"""
    name: str
    total_requests: int = 0
    successes: int = 0
    failures: int = 0
    total_time_s: float = 0.0
    latencies: list[float] = field(default_factory=list)   # ms

    def add(self, result: RequestResult) -> None:
        self.total_requests += 1
        if result.success:
            self.successes += 1
        else:
            self.failures += 1
        self.latencies.append(result.elapsed_ms)

    def report(self) -> str:
        if not self.latencies:
            return f"[{self.name}] 无请求数据"

        lat = sorted(self.latencies)
        n = len(lat)

        avg = statistics.mean(lat)
        p50 = lat[int(n * 0.50)] if n > 0 else 0
        p90 = lat[int(n * 0.90)] if n > 1 else lat[-1]
        p95 = lat[int(n * 0.95)] if n > 1 else lat[-1]
        p99 = lat[min(int(n * 0.99), n - 1)] if n > 1 else lat[-1]
        min_lat = lat[0]
        max_lat = lat[-1]
        std_dev = statistics.stdev(lat) if n > 1 else 0.0
        success_rate = (self.successes / n) * 100 if n > 0 else 0
        throughput = n / self.total_time_s if self.total_time_s > 0 else 0

        lines = [
            f"",
            f"╔══════════════════════════════════════════════════════════════╗",
            f"║  {self.name}",
            f"╠══════════════════════════════════════════════════════════════╣",
            f"║  总请求数:      {n}",
            f"║  成功 / 失败:   {self.successes} / {self.failures}",
            f"║  成功率:        {success_rate:.1f}%",
            f"║  总耗时:        {self.total_time_s:.2f}s",
            f"║  吞吐量:        {throughput:.1f} req/s",
            f"╠══════════════════════════════════════════════════════════════╣",
            f"║  响应时间 (ms):",
            f"║    Min:         {min_lat:.1f}",
            f"║    Avg:         {avg:.1f}",
            f"║    Max:         {max_lat:.1f}",
            f"║    StdDev:      {std_dev:.1f}",
            f"║    P50:         {p50:.1f}",
            f"║    P90:         {p90:.1f}",
            f"║    P95:         {p95:.1f}",
            f"║    P99:         {p99:.1f}",
            f"╚══════════════════════════════════════════════════════════════╝",
            f"",
        ]
        return "\n".join(lines)


# ── 服务可用性检查 ──────────────────────────────────────────────────

async def check_service(host: str) -> bool:
    """检查目标服务是否可用"""
    print(f"🔍 检查服务可用性: {host}/api/v1/health")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{host}/api/v1/health")
            if resp.status_code == 200:
                print(f"✅ 服务可达 (HTTP 200)")
                return True
            else:
                print(f"⚠️  服务返回 HTTP {resp.status_code}")
                return False
    except httpx.ConnectError:
        print(f"❌ 无法连接到 {host}")
        print(f"   请确认服务已启动: uvicorn ragent.main:app --host 0.0.0.0 --port 8000")
        return False
    except Exception as e:
        print(f"❌ 连接错误: {e}")
        return False


# ── Health 压测 ─────────────────────────────────────────────────────

async def bench_health(
    host: str,
    concurrency: int,
    total_requests: int,
) -> Stats:
    """Health 接口并发压测"""
    print(f"\n🚀 Health 压测: {total_requests} 请求, {concurrency} 并发")

    stats = Stats(name="Health Check Benchmark")
    sem = asyncio.Semaphore(concurrency)
    url = f"{host}/api/v1/health"

    async def single_request(client: httpx.AsyncClient) -> RequestResult:
        async with sem:
            start = time.monotonic()
            try:
                resp = await client.get(url)
                elapsed = (time.monotonic() - start) * 1000
                return RequestResult(
                    status_code=resp.status_code,
                    elapsed_ms=elapsed,
                    success=resp.status_code == 200,
                    response_length=len(resp.content),
                )
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                return RequestResult(
                    status_code=0,
                    elapsed_ms=elapsed,
                    success=False,
                    error=str(exc),
                )

    wall_start = time.monotonic()

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [single_request(client) for _ in range(total_requests)]
        # 使用 as_completed 风格 — gather 按完成顺序等待全部
        results = await asyncio.gather(*tasks)

    wall_elapsed = time.monotonic() - wall_start
    stats.total_time_s = wall_elapsed

    for r in results:
        stats.add(r)

    return stats


# ── Chat SSE 压测 ──────────────────────────────────────────────────

async def bench_chat(
    host: str,
    concurrency: int,
    total_requests: int,
) -> Stats:
    """Chat 接口并发压测（SSE 流式）"""
    print(f"\n🚀 Chat 压测: {total_requests} 请求, {concurrency} 并发 (SSE 流式)")

    stats = Stats(name="Chat SSE Benchmark")
    sem = asyncio.Semaphore(concurrency)
    url = f"{host}/api/v1/chat"

    async def single_request(client: httpx.AsyncClient, idx: int) -> RequestResult:
        async with sem:
            question = RAG_QUESTIONS[idx % len(RAG_QUESTIONS)]
            payload = {"question": question}
            start = time.monotonic()
            try:
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers={"Accept": "text/event-stream"},
                    timeout=120.0,
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        elapsed = (time.monotonic() - start) * 1000
                        return RequestResult(
                            status_code=resp.status_code,
                            elapsed_ms=elapsed,
                            success=False,
                            error=f"HTTP {resp.status_code}: {body[:200]}",
                        )

                    full_text = []
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            data_str = line[len("data:"):].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                content = chunk.get("content", "")
                                if content:
                                    full_text.append(content)
                            except json.JSONDecodeError:
                                pass

                    elapsed = (time.monotonic() - start) * 1000
                    return RequestResult(
                        status_code=resp.status_code,
                        elapsed_ms=elapsed,
                        success=True,
                        response_length=len("".join(full_text)),
                    )
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                return RequestResult(
                    status_code=0,
                    elapsed_ms=elapsed,
                    success=False,
                    error=str(exc),
                )

    wall_start = time.monotonic()

    # Chat 请求较慢，显示进度
    async with httpx.AsyncClient(timeout=120.0) as client:
        # 分批发送，每批 concurrency 个
        tasks = []
        for i in range(total_requests):
            tasks.append(single_request(client, i))

        # 逐步完成并报告进度
        results = []
        completed = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            completed += 1
            if completed % max(1, total_requests // 10) == 0 or completed == total_requests:
                print(f"   进度: {completed}/{total_requests} ({completed * 100 // total_requests}%)")

    wall_elapsed = time.monotonic() - wall_start
    stats.total_time_s = wall_elapsed

    for r in results:
        stats.add(r)

    return stats


# ── 主入口 ──────────────────────────────────────────────────────────

async def async_main(args: argparse.Namespace) -> None:
    host = args.host.rstrip("/")

    print("═" * 64)
    print("  RAG Agent Simple Stress Test")
    print("═" * 64)
    print(f"  Target: {host}")
    print(f"  Health: {args.health_concurrency} 并发 × {args.health_requests} 请求")
    print(f"  Chat:   {args.chat_concurrency} 并发 × {args.chat_requests} 请求")
    print("═" * 64)

    # 检查服务
    if not await check_service(host):
        print("\n❌ 服务不可用，退出。请先启动服务后再运行压测。")
        sys.exit(1)

    # ── Health 压测 ──
    if not args.skip_health:
        health_stats = await bench_health(
            host,
            concurrency=args.health_concurrency,
            total_requests=args.health_requests,
        )
        print(health_stats.report())
    else:
        print("\n⏭️  跳过 Health 压测")

    # ── Chat 压测 ──
    if not args.skip_chat:
        chat_stats = await bench_chat(
            host,
            concurrency=args.chat_concurrency,
            total_requests=args.chat_requests,
        )
        print(chat_stats.report())
    else:
        print("\n⏭️  跳过 Chat 压测")

    print("\n✅ 压测完成")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAG Agent 简易压测脚本 (httpx + asyncio)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default="http://localhost:8000",
        help="目标服务地址",
    )
    parser.add_argument(
        "--health-concurrency",
        type=int,
        default=100,
        help="Health 接口并发数",
    )
    parser.add_argument(
        "--health-requests",
        type=int,
        default=1000,
        help="Health 接口总请求数",
    )
    parser.add_argument(
        "--chat-concurrency",
        type=int,
        default=10,
        help="Chat 接口并发数",
    )
    parser.add_argument(
        "--chat-requests",
        type=int,
        default=50,
        help="Chat 接口总请求数",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="跳过 Health 压测",
    )
    parser.add_argument(
        "--skip-chat",
        action="store_true",
        help="跳过 Chat 压测",
    )

    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
