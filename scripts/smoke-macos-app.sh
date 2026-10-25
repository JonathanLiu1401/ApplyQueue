#!/bin/bash
# Smoke-test a packaged macOS ApplyQueue.app:
#   1. every Mach-O in Contents/MacOS contains the expected architecture;
#   2. the bundled backend sidecar passes --self-test (imports + PDF round trip);
#   3. launching the app starts the sidecar and GET /health answers.
# Usage: scripts/smoke-macos-app.sh <path/to/ApplyQueue.app> <x86_64|arm64> [port]
set -euo pipefail

APP="${1:?path to .app}"
ARCH="${2:?expected arch (x86_64 or arm64)}"
PORT="${3:-8743}"
MACOS_DIR="$APP/Contents/MacOS"

[ -d "$MACOS_DIR" ] || { echo "❌ $MACOS_DIR not found" >&2; exit 1; }

echo "== Architectures =="
MAIN_EXE=""
SIDECAR=""
for f in "$MACOS_DIR"/*; do
  archs=$(lipo -archs "$f" 2>/dev/null || true)
  echo "$(basename "$f"): ${archs:-not Mach-O}"
  case " $archs " in
    *" $ARCH "*) ;;
    *) echo "❌ $(basename "$f") lacks $ARCH" >&2; exit 1 ;;
  esac
  case "$(basename "$f")" in
    *-backend*) SIDECAR="$f" ;;
    *) MAIN_EXE="$f" ;;
  esac
done
[ -n "$SIDECAR" ] || { echo "❌ backend sidecar missing from bundle" >&2; exit 1; }
[ -n "$MAIN_EXE" ] || { echo "❌ app executable missing from bundle" >&2; exit 1; }

echo "== Sidecar self-test =="
OUT=$("$SIDECAR" --self-test | tail -1)
echo "$OUT"
echo "$OUT" | grep -q '"pdf": "ok"' || { echo "❌ self-test failed" >&2; exit 1; }
echo "$OUT" | grep -q "\"machine\": \"$ARCH\"" || { echo "❌ sidecar is not running as $ARCH" >&2; exit 1; }

if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "❌ something already answers on port $PORT before launch" >&2; exit 1
fi

echo "== Launch app and wait for /health =="
"$MAIN_EXE" >/tmp/applyqueue-app.log 2>&1 &
APP_PID=$!
cleanup() {
  kill "$APP_PID" 2>/dev/null || true
  pkill -f "$(basename "$SIDECAR")" 2>/dev/null || true
}
trap cleanup EXIT

ok=""
for i in $(seq 1 180); do
  if body=$(curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null); then
    ok=1; break
  fi
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "❌ app exited early" >&2; tail -50 /tmp/applyqueue-app.log >&2; exit 1
  fi
  sleep 1
done
if [ -z "$ok" ]; then
  echo "❌ /health did not answer within 180s" >&2
  tail -50 /tmp/applyqueue-app.log >&2
  exit 1
fi
echo "/health after ${i}s: $body"
SIDECAR_PID=$(pgrep -f "$(basename "$SIDECAR")" | head -1 || true)
if [ -n "$SIDECAR_PID" ]; then
  echo "sidecar pid $SIDECAR_PID arch: $(ps -o arch= -p "$SIDECAR_PID" 2>/dev/null || echo '?')"
fi
echo "✅ Packaged app launched and backend answered /health ($ARCH)"
