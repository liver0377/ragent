#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# stress_test.sh — RAG Agent 一键压测脚本
# ═══════════════════════════════════════════════════════════════════
#
# 用法:
#   bash scripts/stress_test.sh [host] [users] [spawn_rate] [run_time]
#
# 示例:
#   bash scripts/stress_test.sh
#   bash scripts/stress_test.sh http://localhost:8000 50 10 60s
#
# 依次执行:
#   1. 单接口压测（health）   — 50 用户, 30s
#   2. 混合压测              — 60 用户 (10 chat + 50 health), 60s
#   3. 阶梯加压              — 10→100 用户
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

# ── 参数 ────────────────────────────────────────────────────────────
HOST="${1:-http://localhost:8000}"
USERS="${2:-50}"
SPAWN_RATE="${3:-10}"
RUN_TIME="${4:-60s}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCUSTFILE="${SCRIPT_DIR}/../locustfile.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ── 工具函数 ────────────────────────────────────────────────────────
banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║  $1${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_locust() {
    if ! command -v locust &>/dev/null; then
        error "locust 未安装。请执行: pip install locust 或 uv add locust"
        exit 1
    fi
    info "locust version: $(locust --version 2>&1 | head -1)"
}

check_host() {
    info "检查目标服务: ${HOST}/api/v1/health"
    if command -v curl &>/dev/null; then
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${HOST}/api/v1/health" --max-time 5 2>/dev/null || echo "000")
        if [[ "$HTTP_CODE" == "200" ]]; then
            info "服务可达 ✓ (HTTP 200)"
        else
            warn "服务返回 HTTP ${HTTP_CODE}，压测可能失败"
        fi
    fi
}

cleanup_csv() {
    # 清理 locust 之前可能残留的 CSV 文件
    rm -f locust_stats.csv locust_stats_history.csv locust_failures.csv
}

# ── 前置检查 ────────────────────────────────────────────────────────
banner "RAG Agent Stress Test"
info "Host:        ${HOST}"
info "Users:       ${USERS}"
info "Spawn Rate:  ${SPAWN_RATE}"
info "Run Time:    ${RUN_TIME}"
info "Locustfile:  ${LOCUSTFILE}"

check_locust
check_host

if [[ ! -f "${LOCUSTFILE}" ]]; then
    error "locustfile.py 不存在: ${LOCUSTFILE}"
    exit 1
fi

# ════════════════════════════════════════════════════════════════════
# Phase 1: 单接口压测 — Health Check
# ════════════════════════════════════════════════════════════════════
banner "Phase 1/3: 单接口压测 — Health Check (50 users, 30s)"
cleanup_csv

locust \
    -f "${LOCUSTFILE}" \
    --host "${HOST}" \
    --headless \
    --only-summary \
    --users 50 \
    --spawn-rate 10 \
    --run-time 30s \
    --tags health \
    --csv="phase1_health" \
    HealthCheckUser 2>&1 || true

info "Phase 1 完成"
echo ""

# ════════════════════════════════════════════════════════════════════
# Phase 2: 混合压测 — Chat + Health
# ════════════════════════════════════════════════════════════════════
banner "Phase 2/3: 混合压测 — Chat(10) + Health(50), 60s"
cleanup_csv

# Locust 权重控制: HealthCheckUser(5) ChatUser(2) → ~71% health ~29% chat
# 使用 60 总用户 → 大约 43 health + 17 chat
locust \
    -f "${LOCUSTFILE}" \
    --host "${HOST}" \
    --headless \
    --only-summary \
    --users 60 \
    --spawn-rate 10 \
    --run-time 60s \
    --csv="phase2_mixed" \
    HealthCheckUser ChatUser 2>&1 || true

info "Phase 2 完成"
echo ""

# ════════════════════════════════════════════════════════════════════
# Phase 3: 阶梯加压 — 10 → 100 用户 (5 级递增)
# ════════════════════════════════════════════════════════════════════
banner "Phase 3/3: 阶梯加压 — 10 → 100 用户 (MixedUser)"

STEPS=(10 25 50 75 100)
STEP_DURATION=30

for STEP_USERS in "${STEPS[@]}"; do
    info "阶梯加压: ${STEP_USERS} 用户, 持续 ${STEP_DURATION}s"
    cleanup_csv

    locust \
        -f "${LOCUSTFILE}" \
        --host "${HOST}" \
        --headless \
        --only-summary \
        --users "${STEP_USERS}" \
        --spawn-rate 5 \
        --run-time "${STEP_DURATION}s" \
        --csv="phase3_step_${STEP_USERS}" \
        MixedUser 2>&1 || true

    echo ""
done

# ════════════════════════════════════════════════════════════════════
# 汇总
# ════════════════════════════════════════════════════════════════════
banner "压测完成"

echo -e "${YELLOW}CSV 报告已生成:${NC}"
ls -lh phase*_stats.csv 2>/dev/null || warn "未找到 CSV 报告文件"

echo ""
info "查看详细报告:"
info "  cat phase1_health_stats.csv"
info "  cat phase2_mixed_stats.csv"
info "  cat phase3_step_*_stats.csv"
echo ""

# 输出各阶段汇总
for csv in phase*_stats.csv; do
    if [[ -f "$csv" ]]; then
        echo -e "${CYAN}── ${csv} ──${NC}"
        head -3 "$csv"
        echo ""
    fi
done

info "全部压测阶段完成 ✓"
