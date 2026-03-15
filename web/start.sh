#!/usr/bin/env bash
# One-click start for QuantForge Web UI (backend + frontend)
# Usage: ./web/start.sh
#   Stop: ./web/start.sh stop

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE_BACKEND="$ROOT/web/.backend.pid"
PIDFILE_FRONTEND="$ROOT/web/.frontend.pid"
LOG_BACKEND="$ROOT/web/backend.log"
LOG_FRONTEND="$ROOT/web/frontend.log"

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

# Stop any existing instances first
stop_all 2>/dev/null || true

cd "$ROOT"

# Start backend
echo "Starting backend (uvicorn :8000)..."
uv run uvicorn web.backend.main:app --host 0.0.0.0 --port 8000 --reload \
    > "$LOG_BACKEND" 2>&1 &
echo $! > "$PIDFILE_BACKEND"

# Start frontend
echo "Starting frontend (vite :5173)..."
cd "$ROOT/web/frontend"
npx vite --host 0.0.0.0 > "$LOG_FRONTEND" 2>&1 &
echo $! > "$PIDFILE_FRONTEND"

cd "$ROOT"

# Wait for services to come up
echo "Waiting for services..."
for i in $(seq 1 10); do
    if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo ""
echo "==================================="
echo "  QuantForge Web UI is running"
echo "==================================="
echo "  Frontend: http://localhost:5173"
echo "  Backend:  http://localhost:8000"
echo "  API docs: http://localhost:8000/docs"
echo ""
echo "  Logs:"
echo "    Backend:  $LOG_BACKEND"
echo "    Frontend: $LOG_FRONTEND"
echo ""
echo "  Stop: ./web/start.sh stop"
echo "==================================="
