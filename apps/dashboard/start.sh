#!/usr/bin/env bash
# QuantForge Web UI start script.
#
# Usage:
#   ./apps/dashboard/start.sh            — dev mode (uvicorn --reload + vite HMR on :5173)
#   ./apps/dashboard/start.sh --prod     — production mode (vite build, FastAPI serves dist/)
#   ./apps/dashboard/start.sh --https    — HTTPS backend for OAuth callbacks
#   ./apps/dashboard/start.sh --prod --https — production mode over HTTPS
#   ./apps/dashboard/start.sh --host 0.0.0.0 — bind a non-loopback interface
#   ./apps/dashboard/start.sh stop       — stop both services
#
# SECURITY: the backend binds 127.0.0.1 by default so live-trading controls
# are not reachable from the network. If you explicitly bind 0.0.0.0
# (--host 0.0.0.0), set QUANTFORGE_API_KEY in the environment so the backend
# requires an `X-API-Key` header on every /api* request.

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
HTTPS="false"
HOST="127.0.0.1"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --prod|prod) MODE="prod" ;;
        --https) HTTPS="true" ;;
        --host) HOST="${2:?--host requires an IP}"; shift ;;
        *) echo "Unknown argument: $1"; exit 2 ;;
    esac
    shift
done

if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
    echo "WARNING: binding to $HOST exposes live-trading controls to the network."
    # Whitespace-only values are NOT a key: the backend strips too, so a blank
    # key would pass this guard yet install no auth — refuse it.
    if [ -z "$(printf '%s' "${QUANTFORGE_API_KEY:-}" | tr -d '[:space:]')" ]; then
        echo "ERROR: QUANTFORGE_API_KEY is empty; refuse to expose the dashboard unauthenticated."
        echo "       Set QUANTFORGE_API_KEY (and, ideally, use --https) before binding $HOST."
        exit 1
    fi
fi

SSL_ARGS=()
BACKEND_SCHEME="http"
if [ "$HTTPS" = "true" ]; then
    "$ROOT/scripts/setup_local_https.sh"
    SSL_ARGS=(
        --ssl-certfile "$ROOT/.keys/localhost-cert.pem"
        --ssl-keyfile "$ROOT/.keys/localhost-key.pem"
    )
    BACKEND_SCHEME="https"
fi
BACKEND_URL="$BACKEND_SCHEME://localhost:8000"

stop_all 2>/dev/null || true

cd "$ROOT"

if [ "$MODE" = "prod" ]; then
    echo "Building frontend (vite build)..."
    (cd "$APP_DIR/frontend" && npm run build) > "$LOG_FRONTEND" 2>&1
    echo "Starting backend (uvicorn :8000, single worker, prod, serves SPA from dist/)..."
    uv run uvicorn apps.dashboard.backend.main:app \
        --host "$HOST" --port 8000 \
        "${SSL_ARGS[@]}" \
        > "$LOG_BACKEND" 2>&1 &
    echo $! > "$PIDFILE_BACKEND"
    URL_FRONTEND="$BACKEND_URL"
else
    # Dev: limit --reload scope so StatReload does not scan build artifacts
    # or node_modules.
    echo "Starting backend (uvicorn :8000, dev, --reload bounded)..."
    uv run uvicorn apps.dashboard.backend.main:app \
        --host "$HOST" --port 8000 \
        "${SSL_ARGS[@]}" \
        --reload \
        --reload-dir apps/dashboard/backend \
        --reload-dir quantforge \
        --reload-include '*.py' \
        > "$LOG_BACKEND" 2>&1 &
    echo $! > "$PIDFILE_BACKEND"

    echo "Starting frontend (vite :5173)..."
    (cd "$APP_DIR/frontend" && QF_BACKEND_URL="$BACKEND_URL" npx vite --host 0.0.0.0) > "$LOG_FRONTEND" 2>&1 &
    echo $! > "$PIDFILE_FRONTEND"
    URL_FRONTEND="http://localhost:5173"
fi

cd "$ROOT"

echo "Waiting for backend health..."
for i in $(seq 1 15); do
    if curl -sk "$BACKEND_URL/api/health" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo ""
echo "==================================="
echo "  QuantForge Web UI is running ($MODE)"
echo "==================================="
echo "  Frontend: $URL_FRONTEND"
echo "  Backend:  $BACKEND_URL (bind: $HOST)"
echo "  API docs: $BACKEND_URL/docs"
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
    echo "  Auth:     X-API-Key required (QUANTFORGE_API_KEY set)"
fi
echo ""
echo "  Logs:"
echo "    Backend:  $LOG_BACKEND"
echo "    Frontend: $LOG_FRONTEND"
echo ""
echo "  Stop: $0 stop"
echo "==================================="
