#!/usr/bin/env python3
"""Ragent 项目进度报告脚本 — 读取 Git 状态和代码统计，输出结构化进度摘要。"""
import subprocess
import os

PROJECT_DIR = "/root/ragent"
DOCS_DIR = f"{PROJECT_DIR}/docs/python"

# 定义模块开发顺序和状态检测规则
MODULES = [
    {
        "id": "config",
        "name": "配置管理 (config/)",
        "layer": "L1-Framework",
        "check_files": ["src/ragent/config/settings.py"],
    },
    {
        "id": "common-exceptions",
        "name": "异常体系 (common/exceptions)",
        "layer": "L1-Framework",
        "check_files": ["src/ragent/common/exceptions.py"],
    },
    {
        "id": "common-redis",
        "name": "Redis封装 (common/redis_manager)",
        "layer": "L1-Framework",
        "check_files": ["src/ragent/common/redis_manager.py"],
    },
    {
        "id": "common-id",
        "name": "分布式ID (common/snowflake)",
        "layer": "L1-Framework",
        "check_files": ["src/ragent/common/snowflake.py"],
    },
    {
        "id": "common-trace",
        "name": "链路追踪 (common/trace)",
        "layer": "L1-Framework",
        "check_files": ["src/ragent/common/trace.py"],
    },
    {
        "id": "common-context",
        "name": "用户上下文+统一响应 (common/context, common/response)",
        "layer": "L1-Framework",
        "check_files": ["src/ragent/common/context.py", "src/ragent/common/response.py"],
    },
    {
        "id": "common-logging",
        "name": "日志增强+ SSE封装",
        "layer": "L1-Framework",
        "check_files": ["src/ragent/common/logging.py", "src/ragent/common/sse.py"],
    },
    {
        "id": "celery-setup",
        "name": "Celery 任务队列 (common/celery_app)",
        "layer": "L1-Framework",
        "check_files": ["src/ragent/common/celery_app.py"],
    },
    {
        "id": "infra-ai-litellm",
        "name": "litellm 统一调用层",
        "layer": "L2-Infra-AI",
        "check_files": ["src/ragent/infra/ai/llm_service.py", "src/ragent/infra/ai/embedding_service.py"],
    },
    {
        "id": "infra-ai-routing",
        "name": "模型路由 + 熔断",
        "layer": "L2-Infra-AI",
        "check_files": ["src/ragent/infra/ai/model_selector.py", "src/ragent/infra/ai/routing_executor.py"],
    },
    {
        "id": "infra-ai-stream",
        "name": "流式首包探测",
        "layer": "L2-Infra-AI",
        "check_files": ["src/ragent/infra/ai/probe_stream.py"],
    },
    {
        "id": "rag-rewriter",
        "name": "问题重写 (rag/rewriter)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/rag/rewriter/rewriter.py"],
    },
    {
        "id": "rag-intent",
        "name": "意图识别 (rag/intent)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/rag/intent/classifier.py"],
    },
    {
        "id": "rag-retrieval",
        "name": "多路检索 (rag/retrieval)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/rag/retrieval/engine.py"],
    },
    {
        "id": "rag-memory",
        "name": "会话记忆 (rag/memory)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/rag/memory/manager.py"],
    },
    {
        "id": "rag-prompt",
        "name": "Prompt组装 (rag/prompt)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/rag/prompt/builder.py"],
    },
    {
        "id": "rag-chain",
        "name": "RAG问答全链路 (rag/chain)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/rag/chain.py"],
    },
    {
        "id": "ingestion",
        "name": "文档入库流水线 (ingestion/)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/ingestion/pipeline.py", "src/ragent/ingestion/nodes.py"],
    },
    {
        "id": "concurrency",
        "name": "限流排队 (concurrency/)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/concurrency/rate_limiter.py"],
    },
    {
        "id": "data-model",
        "name": "数据模型 (common/models)",
        "layer": "L3-Biz",
        "check_files": ["src/ragent/common/models.py"],
    },
    {
        "id": "app-api",
        "name": "FastAPI 应用入口",
        "layer": "L4-App",
        "check_files": ["src/ragent/main.py", "src/ragent/app/router.py"],
    },
    {
        "id": "app-middleware",
        "name": "中间件 + 监控端点",
        "layer": "L4-App",
        "check_files": ["src/ragent/app/middleware.py"],
    },
    {
        "id": "docker-compose",
        "name": "Docker Compose 部署文件",
        "layer": "L4-App",
        "check_files": ["docker-compose.yml"],
    },
]


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=PROJECT_DIR)
    return r.stdout.strip()


def check_module_done(module):
    for f in module["check_files"]:
        path = os.path.join(PROJECT_DIR, f)
        if os.path.exists(path) and os.path.getsize(path) > 50:
            return True
    return False


def get_git_info():
    total_commits = run("git rev-list --count HEAD")
    recent = run("git log --oneline -10")
    changed = run("git status --short")
    return total_commits, recent, changed


def get_code_stats():
    py_files = run("find src -name '*.py' -not -name '__init__.py' | wc -l")
    py_lines = run("find src -name '*.py' -not -name '__init__.py' -exec cat {} + 2>/dev/null | wc -l")
    empty_inits = run("find src -name '__init__.py' -empty | wc -l")
    return py_files, py_lines, empty_inits


def get_current_branch():
    return run("git branch --show-current")


def main():
    total_commits, recent, changed = get_git_info()
    py_files, py_lines, empty_inits = get_code_stats()
    branch = get_current_branch()

    done = []
    not_done = []
    current = None

    for m in MODULES:
        if check_module_done(m):
            done.append(m)
        else:
            if current is None:
                current = m
            not_done.append(m)

    total = len(MODULES)
    completed = len(done)
    pct = int(completed / total * 100) if total > 0 else 0

    # Build report
    lines = []
    lines.append(f"🤖 **Ragent 开发进度报告**")
    lines.append(f"")
    lines.append(f"📊 **总进度: {completed}/{total} 模块 ({pct}%)**")
    lines.append(f"🔄 分支: `{branch}` | 提交数: {total_commits}")
    lines.append(f"📝 代码文件: {py_files} 个 | 代码行数: {py_lines} 行")
    lines.append(f"")

    if current:
        lines.append(f"🔨 **当前/下一步: {current['name']} ({current['layer']})**")
        lines.append(f"")

    lines.append(f"**✅ 已完成 ({completed}):**")
    if done:
        for m in done:
            lines.append(f"  • {m['name']} [{m['layer']}]")
    else:
        lines.append(f"  （暂无）")

    lines.append(f"")
    lines.append(f"**⬜ 待开发 ({total - completed}):**")
    for m in not_done[:8]:
        lines.append(f"  • {m['name']} [{m['layer']}]")
    if len(not_done) > 8:
        lines.append(f"  ... 还有 {len(not_done) - 8} 个模块")

    if changed:
        lines.append(f"")
        lines.append(f"**📂 未提交变更:**")
        for line in changed.split("\n")[:5]:
            lines.append(f"  `{line}`")
        if len(changed.split("\n")) > 5:
            lines.append(f"  ... 共 {len(changed.split(chr(10)))} 个文件")

    lines.append(f"")
    lines.append(f"**📋 最近提交:**")
    for line in recent.split("\n")[:5]:
        lines.append(f"  `{line}`")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
