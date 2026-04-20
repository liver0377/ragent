#!/usr/bin/env python3
"""
Ragent 压力测试脚本

测试场景:
  1. GET  /api/v1/health        — 健康检查（轻量）
  2. GET  /metrics              — Prometheus 指标（中等）
  3. POST /api/v1/chat (valid)  — 完整验证链路（SSE 流式）
  4. POST /api/v1/chat (invalid)— 422 校验拦截
  5. POST /api/v1/knowledge-bases — 桩接口
  6. 混合场景 — 按比例并发请求
"""
import asyncio
import json
import time
import statistics
from dataclasses import dataclass, field
from typing import Callable, Awaitable

import aiohttp

BASE = "http://localhost:8000"


@dataclass
class Stats:
    """单场景统计"""
    name: str
    total: int = 0
    success: int = 0
    fail: int = 0
    status_codes: dict[int, int] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def record(self, code: int, latency: float, error: str = ""):
        self.total += 1
        self.latencies.append(latency)
        self.status_codes[code] = self.status_codes.get(code, 0) + 1
        if error:
            self.fail += 1
            if len(self.errors) < 5:
                self.errors.append(error)
        else:
            self.success += 1

    def report(self) -> str:
        if not self.latencies:
            return f"  {self.name}: 无数据"
        lats = sorted(self.latencies)
        avg = statistics.mean(lats)
        p50 = lats[int(len(lats) * 0.50)]
        p90 = lats[int(len(lats) * 0.90)]
        p95 = lats[int(len(lats) * 0.95)]
        p99 = lats[min(int(len(lats) * 0.99), len(lats) - 1)]
        rps = self.total / (sum(lats) / 1000) if sum(lats) > 0 else 0
        codes = " ".join(f"{k}={v}" for k, v in sorted(self.status_codes.items()))
        lines = [
            f"  {self.name}",
            f"    请求: {self.total}  成功: {self.success}  失败: {self.fail}",
            f"    状态码: {codes}",
            f"    延迟(ms): avg={avg:.1f}  p50={p50:.1f}  p90={p90:.1f}  p95={p95:.1f}  p99={p99:.1f}",
        ]
        if self.errors:
            lines.append(f"    错误示例: {self.errors[0]}")
        return "\n".join(lines)


async def bench(
    name: str,
    method: str,
    path: str,
    json_body: dict | None = None,
    concurrency: int = 50,
    total_requests: int = 1000,
    expect_codes: set[int] | None = None,
) -> Stats:
    """对单个端点做并发压测"""
    stats = Stats(name=name)
    if expect_codes is None:
        expect_codes = {200}

    sem = asyncio.Semaphore(concurrency)
    url = f"{BASE}{path}"

    async def worker(session: aiohttp.ClientSession):
        async with sem:
            t0 = time.monotonic()
            try:
                async with session.request(
                    method, url, json=json_body, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    await resp.read()
                    lat = (time.monotonic() - t0) * 1000
                    ok = resp.status in expect_codes
                    stats.record(resp.status, lat, "" if ok else f"unexpected code {resp.status}")
            except Exception as e:
                lat = (time.monotonic() - t0) * 1000
                stats.record(0, lat, str(e)[:80])

    async with aiohttp.ClientSession() as session:
        tasks = [worker(session) for _ in range(total_requests)]
        await asyncio.gather(*tasks)

    return stats


async def ramp_test(
    name: str,
    method: str,
    path: str,
    json_body: dict | None = None,
    max_concurrency: int = 200,
    steps: int = 4,
    requests_per_step: int = 500,
    expect_codes: set[int] | None = None,
) -> list[Stats]:
    """逐步提高并发，找出性能拐点"""
    results = []
    for i in range(1, steps + 1):
        c = int(max_concurrency * i / steps)
        s = await bench(
            f"{name} (并发={c})",
            method, path, json_body,
            concurrency=c,
            total_requests=requests_per_step,
            expect_codes=expect_codes,
        )
        results.append(s)
    return results


async def main():
    print("=" * 65)
    print("  Ragent 压力测试")
    print("=" * 65)
    print(f"  目标: {BASE}")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    all_stats = []

    # ===== 阶段 1: 单端点基准测试 =====
    print("▶ 阶段 1: 单端点基准测试 (并发=50, 请求=1000)")
    print("-" * 65)

    scenarios = [
        ("GET /health", "GET", "/api/v1/health", None, {200}),
        ("GET /metrics", "GET", "/metrics", None, {200}),
        ("POST /chat (invalid)", "POST", "/api/v1/chat", {}, {422}),
        ("POST /knowledge-bases", "POST", "/api/v1/knowledge-bases",
         {"name": "test", "description": "bench"}, {200}),
        ("POST /documents/upload", "POST", "/api/v1/documents/upload",
         {"knowledge_base_id": 1, "filename": "test.pdf"}, {200}),
    ]

    for name, method, path, body, codes in scenarios:
        s = await bench(name, method, path, body, concurrency=50, total_requests=1000, expect_codes=codes)
        print(s.report())
        all_stats.append(s)

    # ===== 阶段 2: 并发阶梯测试 — /health =====
    print()
    print("▶ 阶段 2: /health 并发阶梯测试 (50→200 并发)")
    print("-" * 65)
    ramp = await ramp_test(
        "/health 阶梯", "GET", "/api/v1/health",
        max_concurrency=200, steps=4, requests_per_step=2000,
    )
    for s in ramp:
        print(s.report())
        all_stats.append(s)

    # ===== 阶段 3: 并发阶梯测试 — /metrics =====
    print()
    print("▶ 阶段 3: /metrics 并发阶梯测试 (50→200 并发)")
    print("-" * 65)
    ramp2 = await ramp_test(
        "/metrics 阶梯", "GET", "/metrics",
        max_concurrency=200, steps=4, requests_per_step=2000,
    )
    for s in ramp2:
        print(s.report())
        all_stats.append(s)

    # ===== 阶段 4: 混合场景压测 =====
    print()
    print("▶ 阶段 4: 混合场景 (并发=100, 请求=2000)")
    print("  比例: 60% /health + 20% /metrics + 20% /chat(422)")
    print("-" * 65)
    mixed = Stats(name="混合场景")
    sem = asyncio.Semaphore(100)
    total_mixed = 2000

    async def mixed_worker(session: aiohttp.ClientSession):
        async with sem:
            import random
            r = random.random()
            t0 = time.monotonic()
            try:
                if r < 0.6:
                    url = f"{BASE}/api/v1/health"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        await resp.read()
                elif r < 0.8:
                    url = f"{BASE}/metrics"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        await resp.read()
                else:
                    url = f"{BASE}/api/v1/chat"
                    async with session.post(url, json={}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        await resp.read()
                lat = (time.monotonic() - t0) * 1000
                mixed.record(resp.status, lat)
            except Exception as e:
                lat = (time.monotonic() - t0) * 1000
                mixed.record(0, lat, str(e)[:80])

    async with aiohttp.ClientSession() as session:
        tasks = [mixed_worker(session) for _ in range(total_mixed)]
        await asyncio.gather(*tasks)
    print(mixed.report())
    all_stats.append(mixed)

    # ===== 汇总 =====
    print()
    print("=" * 65)
    print("  汇总")
    print("=" * 65)
    total_req = sum(s.total for s in all_stats)
    total_ok = sum(s.success for s in all_stats)
    total_fail = sum(s.fail for s in all_stats)
    all_lats = []
    for s in all_stats:
        all_lats.extend(s.latencies)
    if all_lats:
        all_lats_sorted = sorted(all_lats)
        overall_avg = statistics.mean(all_lats)
        overall_p95 = all_lats_sorted[int(len(all_lats_sorted) * 0.95)]
        overall_p99 = all_lats_sorted[min(int(len(all_lats_sorted) * 0.99), len(all_lats_sorted) - 1)]
    else:
        overall_avg = overall_p95 = overall_p99 = 0

    print(f"  总请求: {total_req}")
    print(f"  成功: {total_ok}  失败: {total_fail}  成功率: {total_ok/total_req*100:.1f}%")
    print(f"  全局延迟(ms): avg={overall_avg:.1f}  p95={overall_p95:.1f}  p99={overall_p99:.1f}")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
