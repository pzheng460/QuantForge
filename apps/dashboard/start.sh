#!/usr/bin/env bash
# QuantForge Web UI start script.
#
# Usage:
#   ./apps/dashboard/start.sh            — dev mode (uvicorn --reload + vite HMR on :5173)
#   ./apps/dashboard/start.sh --prod     — production mode (vite build, FastAPI serves dist/)
#   ./apps/dashboard/start.sh stop       — stop both services

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_DIR="$ROOT/apps/dashboard"
PIDFILE_BACKEND="$APP_DIR/.backend.pid"
PIDFILE_FRONTEND="$APP_DIR/.frontend.pid"
LOG_BACKEND="$APP_DIR/backend.log"
LOG_FRONTEND="$APP_DIR/frontend.log"

stop_all() {
    for pidfile in "$PIDFILE_BACKEND" "$PIDFILE_FRONTEND"; do
        if [ -f "$pidfile" ]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null
                echo "Stopped PID $pid"
            fi
            rm -f "$pidfile"
        fi
    done
    echo "All services stopped."
}

if [ "${1:-}" = "stop" ]; then
    stop_all
    exit 0
fi

MODE="dev"
if [ "${1:-}" = "--prod" ] || [ "${1:-}" = "prod" ]; then
    MODE="prod"
fi

stop_all 2>/dev/null || true

cd "$ROOT"

if [ "$MODE" = "prod" ]; then
    echo "Building frontend (vite build)..."
    (cd "$APP_DIR/frontend" && npm run build) > "$LOG_FRONTEND" 2>&1
    echo "Starting backend (uvicorn :8000, single worker, prod, serves SPA from dist/)..."
    uv run uvicorn apps.dashboard.backend.main:app \
        --host 0.0.0.0 --port 8000 \
        > "$LOG_BACKEND" 2>&1 &
    echo $! > "$PIDFILE_BACKEND"
    URL_FRONTEND="http://localhost:8000"
else
    # Dev: limit --reload scope so StatReload doesn't eat CPU scanning
    # eval/ artifacts, node_modules, or agent_jobs JSON writes.
    echo "Starting backend (uvicorn :8000, dev, --reload bounded)..."
    uv run uvicorn apps.dashboard.backend.main:app \
        --host 0.0.0.0 --port 8000 \
        --reload \
        --reload-dir apps/dashboard/backend \
        --reload-dir quantforge \
        --reload-include '*.py' \
        > "$LOG_BACKEND" 2>&1 &
    echo $! > "$PIDFILE_BACKEND"

    echo "Starting frontend (vite :5173)..."
    (cd "$APP_DIR/frontend" && npx vite --host 0.0.0.0) > "$LOG_FRONTEND" 2>&1 &
    echo $! > "$PIDFILE_FRONTEND"
    URL_FRONTEND="http://localhost:5173"
fi

cd "$ROOT"

echo "Waiting for backend health..."
for i in $(seq 1 15); do
    if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo ""
echo "==================================="
echo "  QuantForge Web UI is running ($MODE)"
echo "==================================="
echo "  Frontend: $URL_FRONTEND"
echo "  Backend:  http://localhost:8000"
echo "  API docs: http://localhost:8000/docs"
echo ""
echo "  Logs:"
echo "    Backend:  $LOG_BACKEND"
echo "    Frontend: $LOG_FRONTEND"
echo ""
echo "  Stop: $0 stop"
echo "==================================="
