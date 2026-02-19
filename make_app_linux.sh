#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="Instagiffer"
name="instagiffer"

echo "==> Activating venv ..."
VENV="$SCRIPT_DIR/.venv"
if [[ -f "$VENV/bin/activate" ]]; then
    source "$VENV/bin/activate"
else
    echo "ERROR: No venv found at $VENV — run 'uv sync' first."
    exit 1
fi


export INSTAGIFFER_VERSION="${INSTAGIFFER_VERSION:-$(python3 -c "
import tomllib
with open('$SCRIPT_DIR/pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")}"
BUILD_SCRIPT="$SCRIPT_DIR/setup-cx_freeze.py"


echo "==> Checking dependencies ..."
for cmd in python3 fpm; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: '$cmd' not found. Please install it first."
        [[ "$cmd" == "fpm" ]] && echo "  Install fpm: sudo apt install ruby-dev build-essential && sudo gem install fpm"
        exit 1
    fi
done

echo "==> Building $NAME v$INSTAGIFFER_VERSION for Linux ..."
python3 "$BUILD_SCRIPT" build

BUILD_DIR="$SCRIPT_DIR/build"
# Find the build output
CX_BUILD_DIR="$(find "$SCRIPT_DIR/build" -maxdepth 1 -name 'exe.linux-*' | head -1)"
if [[ -z "$CX_BUILD_DIR" ]]; then
    echo "ERROR: Could not find cx_Freeze output in build/"
    exit 1
fi

EXE="$(find "$CX_BUILD_DIR" -name $name -type f | head -1)"
if [[ -n "$EXE" ]]; then
    chmod +x "$EXE"
    echo "==> cx_Freeze build complete: $EXE"
else
    echo "ERROR: Could not find built executable"
    exit 1
fi


DESKTOP_FILE="$BUILD_DIR/$name.desktop"
echo "==> Creating desktop file $DESKTOP_FILE ..."
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=$NAME
Comment=Create optimized GIFs from videos, images, and screen captures
Exec=/opt/instagiffer/instagiffer
Icon=instagiffer
Type=Application
Categories=Graphics;Video;
Terminal=false
EOF


POST_INSTALL="$BUILD_DIR/post-install.sh"
echo "==> Creating post-install script $POST_INSTALL ..."
cat > "$POST_INSTALL" <<'EOF'
#!/bin/bash
ln -sf /opt/instagiffer/instagiffer /usr/local/bin/instagiffer
update-desktop-database /usr/share/applications/ 2>/dev/null || true
gtk-update-icon-cache /usr/share/icons/hicolor/ 2>/dev/null || true
EOF
chmod +x "$POST_INSTALL"

# Build .deb package
DEB_OUT="$SCRIPT_DIR/dist/instagiffer_${INSTAGIFFER_VERSION}_amd64.deb"
echo "==> Building .deb package ..."
mkdir -p "$SCRIPT_DIR/dist"

fpm -s dir -t deb \
    -n instagiffer \
    -v "$INSTAGIFFER_VERSION" \
    -a amd64 \
    -d ffmpeg \
    -d imagemagick \
    --description "Create optimized GIFs from videos, images, and screen captures" \
    --maintainer "Eric Werner <ewerybody@gmail.com>" \
    --url "https://github.com/ewerybody/instagiffer" \
    --license "BSD-4-Clause" \
    --after-install "$POST_INSTALL" \
    -p "$DEB_OUT" \
    --force \
    "$CX_BUILD_DIR/=/opt/instagiffer/" \
    "$DESKTOP_FILE=/usr/share/applications/instagiffer.desktop" \
    "$SCRIPT_DIR/doc/graphics/logo.png=/usr/share/icons/hicolor/256x256/apps/instagiffer.png"

echo "==> Done! Package ready at: $DEB_OUT"
echo ""
echo "Install with:"
echo "  sudo dpkg -i $DEB_OUT"
echo "  sudo apt-get install -f  # pulls in ffmpeg & imagemagick if missing"