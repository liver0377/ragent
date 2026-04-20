#!/usr/bin/env python3
"""精简压测 — 用于 uvicorn vs gunicorn 对比"""
import asyncio
import json
import statistics
import time
import aiohttp

BASE = "http://localhost:8000"


async def bench(name, method, path, json_body=None, concurrency=100, total=2000, expect_codes=None):
    if expect_codes is None:
        expect_codes = {200}
    sem = asyncio.Semaphore(concurrency)
    url = f"{BASE}{path}"
    latencies = []
    errors = []
    status_codes = {}

    async def worker(session):
        async with sem:
            t0 = time.monotonic()
            try:
                async with session.request(method, url, json=json_body, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    await resp.read()
                    lat = (time.monotonic() - t0) * 1000
                    latencies.append(lat)
                    status_codes[resp.status] = status_codes.get(resp.status, 0) + 1
                    if resp.status not in expect_codes:
                        errors.append(f"code={resp.status}")
            except Exception as e:
                lat = (time.monotonic() - t0) * 1000
                latencies.append(lat)
                errors.append(str(e)[:60])
                status_codes[0] = status_codes.get(0, 0) + 1

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*[worker(session) for _ in range(total)])

    latencies.sort()
    n = len(latencies)
    return {
        "name": name,
        "total": total,
        "errors": len(errors),
        "codes": status_codes,
        "avg": f"{statistics.mean(latencies):.1f}",
        "p50": f"{latencies[int(n*0.50)]:.1f}",
        "p90": f"{latencies[int(n*0.90)]:.1f}",
        "p95": f"{latencies[int(n*0.95)]:.1f}",
        "p99": f"{latencies[min(int(n*0.99), n-1)]:.1f}",
        "max": f"{latencies[-1]:.1f}",
    }


async def run_all():
    print("=" * 70)
    print(f"  压力测试 — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    scenarios = [
        ("GET /health (c=100)", "GET", "/api/v1/health", None, 100, 2000, {200}),
        ("GET /health (c=200)", "GET", "/api/v1/health", None, 200, 2000, {200}),
        ("GET /health (c=400)", "GET", "/api/v1/health", None, 400, 2000, {200}),
        ("GET /metrics (c=100)", "GET", "/metrics", None, 100, 2000, {200}),
        ("GET /metrics (c=200)", "GET", "/metrics", None, 200, 2000, {200}),
        ("POST /chat 422 (c=100)", "POST", "/api/v1/chat", {}, 100, 2000, {422}),
        ("POST /chat 422 (c=200)", "POST", "/api/v1/chat", {}, 200, 2000, {422}),
    ]

    results = []
    for name, method, path, body, c, total, codes in scenarios:
        r = await bench(name, method, path, body, c, total, codes)
        results.append(r)
        codes_str = " ".join(f"{k}={v}" for k, v in sorted(r["codes"].items()))
        err_str = f"  err={r['errors']}" if r["errors"] else ""
        print(f"  {r['name']:28s} avg={r['avg']:>8s}ms  p50={r['p50']:>8s}ms  p95={r['p95']:>8s}ms  p99={r['p99']:>8s}ms  max={r['max']:>8s}ms  [{codes_str}]{err_str}")

    print("=" * 70)
    return results


if __name__ == "__main__":
    asyncio.run(run_all())
