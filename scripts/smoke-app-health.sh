#!/bin/bash
# Launch a packaged ApplyQueue app and wait until the backend sidecar it spawned answers
# GET /health as the frozen (bundled) backend. Used by CI on Linux; macOS has
# scripts/smoke-macos-app.sh (adds architecture checks), Windows scripts/smoke-windows-app.ps1.
# Usage: scripts/smoke-app-health.sh <command> [args...]
#   e.g. xvfb-run -a scripts/smoke-app-health.sh ./ApplyQueue_0.1.0_amd64.AppImage
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: $0 <command> [args...]" >&2; exit 2; }
PORT="${APPLYQUEUE_SMOKE_PORT:-8743}"
TIMEOUT="${APPLYQUEUE_SMOKE_TIMEOUT:-180}"
LOG="${RUNNER_TEMP:-/tmp}/applyqueue-app-smoke.log"

if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "❌ something already answers on port $PORT before launch" >&2; exit 1
fi

"$@" >"$LOG" 2>&1 &
APP_PID=$!
cleanup() {
  kill "$APP_PID" 2>/dev/null || true
  pkill -f 'applyqueue-backend' 2>/dev/null || true
}
trap cleanup EXIT

body=""
for i in $(seq 1 "$TIMEOUT"); do
  if body=$(curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null); then break; fi
  body=""
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "❌ app exited early" >&2; tail -50 "$LOG" >&2; exit 1
  fi
  sleep 1
done
if [ -z "$body" ]; then
  echo "❌ /health did not answer within ${TIMEOUT}s" >&2; tail -50 "$LOG" >&2; exit 1
fi
echo "/health after ${i}s: $body"
echo "$body" | grep -q '"app":"applyqueue"' || { echo "❌ not the ApplyQueue backend" >&2; exit 1; }
echo "$body" | grep -q '"frozen":true' || { echo "❌ backend is not the bundled (frozen) sidecar" >&2; exit 1; }
echo "✅ Packaged app launched and its bundled backend answered /health"
