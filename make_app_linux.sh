#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Activate venv ───────────────────────────────────────
VENV="$SCRIPT_DIR/.venv"
if [[ -f "$VENV/bin/activate" ]]; then
    echo "==> Activating venv..."
    source "$VENV/bin/activate"
else
    echo "ERROR: No venv found at $VENV — run 'uv sync' first."
    exit 1
fi

# ── Config ──────────────────────────────────────────────
export INSTAGIFFER_VERSION="${INSTAGIFFER_VERSION:-$(python3 -c "
import tomllib
with open('$SCRIPT_DIR/pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")}"
BUILD_SCRIPT="$SCRIPT_DIR/setup-cx_freeze.py"

# ── Preflight checks ───────────────────────────────────
echo "==> Checking dependencies..."
for cmd in python3 ffmpeg convert; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: '$cmd' not found. Please install it first."
        exit 1
    fi
done

echo "==> Building Instagiffer v$INSTAGIFFER_VERSION for Linux..."
python3 "$BUILD_SCRIPT" build

# ── Make executable ─────────────────────────────────────
EXE="$(find "$SCRIPT_DIR/build" -name "instagiffer" -type f | head -1)"
if [[ -n "$EXE" ]]; then
    chmod +x "$EXE"
    echo "==> Build complete: $EXE"
else
    echo "WARNING: Could not find built executable in build/"
fi