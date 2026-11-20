#!/usr/bin/env bash
# One-time setup for Linux (Ubuntu etc.): creates a venv and a desktop icon.
set -e
cd "$(dirname "$(readlink -f "$0")")"
DIR="$(pwd)"

command -v python3 >/dev/null || { echo "Install python3 first (sudo apt install python3 python3-venv)"; exit 1; }
echo "[1/3] Creating Python environment..."
[ -d .venv ] || python3 -m venv .venv
echo "[2/3] Installing libraries..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
# Native window needs Qt; if this fails the app opens in a Chrome/Edge app window instead.
.venv/bin/pip install -q "pywebview[qt]>=5" || echo "   (no native window - will use Chrome/Chromium app window)"
[ -f .env ] || cp .env.example .env

echo "[3/3] Creating desktop icon..."
ENTRY="[Desktop Entry]
Type=Application
Name=ekagra
Comment=Find the best video for a topic and read its summary
Exec=\"$DIR/.venv/bin/python\" \"$DIR/launcher.py\"
Path=$DIR
Icon=$DIR/icon.png
Terminal=false
Categories=Education;"
mkdir -p ~/.local/share/applications
echo "$ENTRY" > ~/.local/share/applications/studytube.desktop
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESK" ]; then
  echo "$ENTRY" > "$DESK/studytube.desktop"
  chmod +x "$DESK/studytube.desktop"
  gio set "$DESK/studytube.desktop" metadata::trusted true 2>/dev/null || true
fi
echo "Done. Double-click ekagra on your Desktop (or find it in the app menu)."
