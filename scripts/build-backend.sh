#!/bin/bash
# Build the Python backend into a standalone executable using PyInstaller.
# The output binary is placed in src-tauri/binaries/ for Tauri sidecar bundling.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "🔨 Building Python backend with PyInstaller..."

# Use target triple from argument, or auto-detect from host
if [ -n "$1" ]; then
  TARGET="$1"
else
  ARCH=$(uname -m)
  OS=$(uname -s)
  if [ "$OS" = "Darwin" ]; then
    if [ "$ARCH" = "arm64" ]; then
      TARGET="aarch64-apple-darwin"
    else
      TARGET="x86_64-apple-darwin"
    fi
  elif [ "$OS" = "Linux" ]; then
    TARGET="x86_64-unknown-linux-gnu"
  else
    TARGET="x86_64-pc-windows-msvc"
  fi
fi

# PyInstaller cannot cross-compile: the sidecar must be built on a host of the
# target architecture (Intel Macs need a native x86_64 build, e.g. macos-15-intel).
if [ "$(uname -s)" = "Darwin" ]; then
  HOST_ARCH=$(uv run python -c "import platform; print(platform.machine())")
  case "$TARGET" in
    x86_64-apple-darwin) WANT=x86_64 ;;
    aarch64-apple-darwin) WANT=arm64 ;;
    *) WANT="" ;;
  esac
  if [ -n "$WANT" ] && [ "$HOST_ARCH" != "$WANT" ]; then
    echo "❌ Target $TARGET needs a $WANT Python, but uv's Python is $HOST_ARCH." >&2
    exit 1
  fi
fi

# Build with PyInstaller
uv run python -m PyInstaller \
  --onefile \
  --name "applyqueue-backend-${TARGET}" \
  --distpath src-tauri/binaries \
  --workpath build/pyinstaller \
  --specpath build \
  --paths "${PROJECT_DIR}/backend" \
  --add-data "${PROJECT_DIR}/backend/core:core" \
  --add-data "${PROJECT_DIR}/backend/memory:memory" \
  --add-data "${PROJECT_DIR}/backend/sources:sources" \
  --add-data "${PROJECT_DIR}/backend/sources/plugins:sources/plugins" \
  --add-data "${PROJECT_DIR}/cli:cli" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.http.h11_impl \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import fastapi \
  --hidden-import langchain_openai \
  --hidden-import langchain_anthropic \
  --hidden-import langchain_aws \
  --hidden-import browser_use \
  --hidden-import browser_use.agent \
  --hidden-import browser_use.browser \
  --hidden-import browser_use.llm \
  --hidden-import playwright \
  --hidden-import playwright.async_api \
  --hidden-import filelock \
  --hidden-import pydantic_settings \
  --hidden-import psutil \
  --hidden-import yaml \
  --hidden-import tzdata \
  --collect-data tzdata \
  --collect-all browser_use \
  --collect-all playwright \
  --hidden-import pdfminer.high_level \
  --collect-data pdfminer \
  --collect-data reportlab \
  --clean \
  backend/main.py

echo "✅ Backend binary built: src-tauri/binaries/applyqueue-backend-${TARGET}"
ls -lh "src-tauri/binaries/applyqueue-backend-${TARGET}"
if [ "$(uname -s)" = "Darwin" ]; then
  echo "Architectures: $(lipo -archs "src-tauri/binaries/applyqueue-backend-${TARGET}")"
fi

# Sign the sidecar binary with entitlements (macOS only)
# This allows the PyInstaller binary to load its extracted Python.framework
# without macOS Sequoia blocking the unsigned dlopen()
if [ "$(uname -s)" = "Darwin" ]; then
  BINARY="src-tauri/binaries/applyqueue-backend-${TARGET}"
  ENTITLEMENTS="src-tauri/Entitlements.plist"

  if [ -f "$ENTITLEMENTS" ]; then
    if security find-identity -v -p codesigning 2>/dev/null | grep -q "Developer ID\|Apple Development"; then
      IDENTITY=$(security find-identity -v -p codesigning | grep -E "Developer ID|Apple Development" | head -1 | awk -F'"' '{print $2}')
      echo "🔏 Signing sidecar with identity: $IDENTITY"
      codesign --force --options runtime --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$BINARY"
      echo "✅ Sidecar signed with entitlements (disable-library-validation)"
    else
      echo "⚠️  No signing identity found — signing ad-hoc with entitlements (local dev)"
      codesign --force --options runtime --entitlements "$ENTITLEMENTS" --sign - "$BINARY"
      echo "✅ Sidecar ad-hoc signed with entitlements"
    fi
  else
    echo "⚠️  Entitlements.plist not found — skipping sidecar signing"
  fi
fi

