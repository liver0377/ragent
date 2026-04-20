#!/bin/bash
# ===========================================================================
# Ragent 一键启动脚本 — 直接在服务器上启动（不依赖 Docker）
# 用法: ./deploy/start.sh {start|stop|restart|status}
# ===========================================================================

set -euo pipefail

# ---------- 配置 ----------
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
LOG_DIR="${PROJECT_DIR}/logs"
PID_DIR="${PROJECT_DIR}/.pids"

API_HOST="0.0.0.0"
API_PORT=8000
CELERY_CONCURRENCY=4
CELERY_LOGLEVEL="info"

API_PID_FILE="${PID_DIR}/api.pid"
WORKER_PID_FILE="${PID_DIR}/worker.pid"
API_LOG="${LOG_DIR}/api.log"
WORKER_LOG="${LOG_DIR}/worker.log"

# ---------- Redis 二进制路径（直接调用，避免 snap fork bomb） ----------
REDIS_BIN="/snap/redis/current/usr/bin/redis-server"
REDIS_CLI="/snap/redis/current/usr/bin/redis-cli"

# ---------- 颜色 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_blue()  { echo -e "${BLUE}[INFO]${NC}  $*"; }

# ---------- 初始化 ----------
init_dirs() {
    mkdir -p "${LOG_DIR}" "${PID_DIR}"
}

load_env() {
    if [[ -f "${ENV_FILE}" ]]; then
        set -a
        source "${ENV_FILE}"
        set +a
        log_info "已加载环境变量: ${ENV_FILE}"
    else
        log_warn "未找到 .env 文件: ${ENV_FILE}"
    fi
}

# ---------- Redis ----------
check_redis() {
    if ${REDIS_CLI} ping > /dev/null 2>&1; then
        log_info "Redis 正在运行"
        return 0
    fi
    return 1
}

start_redis() {
    log_info "正在启动 Redis..."
    # 直接使用 Redis 二进制启动（绝对不使用 snap 命令，避免 fork bomb）
    if ! check_redis; then
        if [[ -x "${REDIS_BIN}" ]]; then
            ${REDIS_BIN} --daemonize yes --save "" --appendonly no
        else
            log_error "Redis 二进制不存在或不可执行: ${REDIS_BIN}"
            log_error "请安装 snap redis: snap install redis"
            return 1
        fi
    fi
    # 等待 Redis 启动
    local retries=10
    while ! check_redis && [[ $retries -gt 0 ]]; do
        sleep 1
        retries=$((retries - 1))
    done
    if check_redis; then
        log_info "Redis 已启动"
    else
        log_error "Redis 启动失败，请手动检查"
        return 1
    fi
}

stop_redis() {
    if ! check_redis; then
        log_warn "Redis 未运行"
        return 0
    fi
    log_info "正在停止 Redis..."
    ${REDIS_CLI} shutdown 2>/dev/null || true
    # 等待 Redis 关闭
    local retries=10
    while check_redis && [[ $retries -gt 0 ]]; do
        sleep 1
        retries=$((retries - 1))
    done
    if ! check_redis; then
        log_info "Redis 已停止"
    else
        log_error "Redis 停止失败，请手动检查"
        return 1
    fi
}

# ---------- Celery Worker ----------
start_worker() {
    if is_running "${WORKER_PID_FILE}"; then
        log_warn "Celery Worker 已在运行 (PID: $(cat "${WORKER_PID_FILE}"))"
        return 0
    fi
    log_info "正在启动 Celery Worker (并发: ${CELERY_CONCURRENCY})..."
    cd "${PROJECT_DIR}"
    nohup celery -A ragent.common.celery_app worker \
        -l "${CELERY_LOGLEVEL}" \
        -c "${CELERY_CONCURRENCY}" \
        --pidfile="${WORKER_PID_FILE}" \
        >> "${WORKER_LOG}" 2>&1 &
    echo $! > "${WORKER_PID_FILE}"
    sleep 2
    if is_running "${WORKER_PID_FILE}"; then
        log_info "Celery Worker 已启动 (PID: $(cat "${WORKER_PID_FILE}"))"
    else
        log_error "Celery Worker 启动失败，请查看日志: ${WORKER_LOG}"
        return 1
    fi
}

stop_worker() {
    if ! is_running "${WORKER_PID_FILE}"; then
        log_warn "Celery Worker 未运行"
        return 0
    fi
    log_info "正在停止 Celery Worker..."
    local pid
    pid=$(cat "${WORKER_PID_FILE}")
    kill -TERM "${pid}" 2>/dev/null || true
    # 等待进程退出
    local retries=15
    while kill -0 "${pid}" 2>/dev/null && [[ $retries -gt 0 ]]; do
        sleep 1
        retries=$((retries - 1))
    done
    if kill -0 "${pid}" 2>/dev/null; then
        kill -9 "${pid}" 2>/dev/null || true
    fi
    rm -f "${WORKER_PID_FILE}"
    log_info "Celery Worker 已停止"
}

# ---------- FastAPI ----------
start_api() {
    if is_running "${API_PID_FILE}"; then
        log_warn "FastAPI 服务已在运行 (PID: $(cat "${API_PID_FILE}"))"
        return 0
    fi
    log_info "正在启动 FastAPI 服务 (${API_HOST}:${API_PORT})..."
    cd "${PROJECT_DIR}"
    nohup uvicorn ragent.main:app \
        --host "${API_HOST}" \
        --port "${API_PORT}" \
        --pidfile="${API_PID_FILE}" \
        >> "${API_LOG}" 2>&1 &
    echo $! > "${API_PID_FILE}"
    # 等待 API 启动
    local retries=20
    while ! curl -sf "http://localhost:${API_PORT}/api/v1/health" > /dev/null 2>&1 && [[ $retries -gt 0 ]]; do
        sleep 1
        retries=$((retries - 1))
    done
    if curl -sf "http://localhost:${API_PORT}/api/v1/health" > /dev/null 2>&1; then
        log_info "FastAPI 服务已启动 (PID: $(cat "${API_PID_FILE}"))"
    else
        log_error "FastAPI 服务启动失败，请查看日志: ${API_LOG}"
        return 1
    fi
}

stop_api() {
    if ! is_running "${API_PID_FILE}"; then
        log_warn "FastAPI 服务未运行"
        return 0
    fi
    log_info "正在停止 FastAPI 服务..."
    local pid
    pid=$(cat "${API_PID_FILE}")
    kill -TERM "${pid}" 2>/dev/null || true
    local retries=15
    while kill -0 "${pid}" 2>/dev/null && [[ $retries -gt 0 ]]; do
        sleep 1
        retries=$((retries - 1))
    done
    if kill -0 "${pid}" 2>/dev/null; then
        kill -9 "${pid}" 2>/dev/null || true
    fi
    rm -f "${API_PID_FILE}"
    log_info "FastAPI 服务已停止"
}

# ---------- 通用工具 ----------
is_running() {
    local pid_file="$1"
    if [[ -f "${pid_file}" ]]; then
        local pid
        pid=$(cat "${pid_file}")
        if kill -0 "${pid}" 2>/dev/null; then
            return 0
        fi
        rm -f "${pid_file}"
    fi
    return 1
}

# ---------- 命令 ----------
cmd_start() {
    log_blue "=========================================="
    log_blue "  Ragent 服务启动"
    log_blue "=========================================="
    init_dirs
    load_env
    start_redis
    start_worker
    start_api
    echo ""
    cmd_status
}

cmd_stop() {
    log_blue "=========================================="
    log_blue "  Ragent 服务停止"
    log_blue "=========================================="
    stop_api
    stop_worker
    stop_redis
    echo ""
    log_info "所有服务已停止"
}

cmd_restart() {
    log_blue "=========================================="
    log_blue "  Ragent 服务重启"
    log_blue "=========================================="
    cmd_stop
    echo ""
    cmd_start
}

cmd_status() {
    log_blue "=========================================="
    log_blue "  Ragent 服务状态"
    log_blue "=========================================="
    echo ""

    # Redis
    if check_redis; then
        local redis_info
        redis_info=$(${REDIS_CLI} info server 2>/dev/null | grep "redis_version" | cut -d: -f2 | tr -d '\r')
        log_info "Redis:       ${GREEN}运行中${NC}  (版本: ${redis_info})"
    else
        log_info "Redis:       ${RED}未运行${NC}"
    fi

    # Celery Worker
    if is_running "${WORKER_PID_FILE}"; then
        log_info "Celery Worker: ${GREEN}运行中${NC}  (PID: $(cat "${WORKER_PID_FILE}"))"
    else
        log_info "Celery Worker: ${RED}未运行${NC}"
    fi

    # FastAPI
    if is_running "${API_PID_FILE}"; then
        local health
        health=$(curl -sf "http://localhost:${API_PORT}/api/v1/health" 2>/dev/null || echo "unreachable")
        log_info "FastAPI API:  ${GREEN}运行中${NC}  (PID: $(cat "${API_PID_FILE}"))  health: ${health}"
    else
        log_info "FastAPI API:  ${RED}未运行${NC}"
    fi

    echo ""
    log_blue "访问地址:"
    echo "  API:      http://localhost:${API_PORT}"
    echo "  健康检查: http://localhost:${API_PORT}/api/v1/health"
    echo "  Chat:     http://localhost:${API_PORT}/api/v1/chat"
    echo "  API文档:  http://localhost:${API_PORT}/docs"
    echo ""
    log_blue "日志文件:"
    echo "  API:      ${API_LOG}"
    echo "  Worker:   ${WORKER_LOG}"
}

# ---------- 主入口 ----------
case "${1:-}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    restart) cmd_restart ;;
    status)  cmd_status  ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        echo ""
        echo "命令:"
        echo "  start    启动所有服务 (Redis + Celery Worker + FastAPI)"
        echo "  stop     停止所有服务"
        echo "  restart  重启所有服务"
        echo "  status   查看服务状态"
        exit 1
        ;;
esac
