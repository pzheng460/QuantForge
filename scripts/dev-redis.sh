#!/usr/bin/env bash
# Start / stop / query the local Redis built from official source under
# ~/.quantforge/redis (no root required). Loopback-only, no password.
#
#   scripts/dev-redis.sh start   # start (daemonized, pidfile-based)
#   scripts/dev-redis.sh stop
#   scripts/dev-redis.sh status  # redis-cli PING
set -euo pipefail

BIN="$HOME/.quantforge/redis/bin"
CONF="$HOME/.quantforge/redis/redis-dev.conf"
PIDFILE="$HOME/.quantforge/redis/redis.pid"
DATA_DIR="$HOME/.quantforge/redis-data"
HOST=127.0.0.1
PORT=6379

cmd="${1:-start}"
case "$cmd" in
  start)
    if [ ! -x "$BIN/redis-server" ]; then
      echo "redis binaries not built — run the install steps in README first" >&2
      exit 1
    fi
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "redis already running (pid $(cat "$PIDFILE"))"
      exit 0
    fi
    mkdir -p "$DATA_DIR"
    "$BIN/redis-server" "$CONF" --daemonize yes --pidfile "$PIDFILE"
    sleep 0.3
    "$BIN/redis-cli" -h "$HOST" -p "$PORT" ping
    echo "redis started: pid $(cat "$PIDFILE"), $HOST:$PORT, data in $DATA_DIR"
    ;;
  stop)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      kill "$(cat "$PIDFILE")"
      rm -f "$PIDFILE"
      echo "redis stopped"
    else
      echo "redis not running"
    fi
    ;;
  status)
    "$BIN/redis-cli" -h "$HOST" -p "$PORT" ping 2>/dev/null || echo "not running ($HOST:$PORT)"
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
