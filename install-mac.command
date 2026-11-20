#!/bin/bash
# One-time setup for macOS: double-click this file (right-click > Open the first time).
set -e
cd "$(dirname "$0")"; DIR="$(pwd)"
command -v python3 >/dev/null || { echo "Install Python 3 from python.org first"; read; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt "pywebview>=5"
[ -f .env ] || cp .env.example .env
cat > ~/Desktop/ekagra.command <<EOF
#!/bin/bash
cd "$DIR" && exec "$DIR/.venv/bin/python" "$DIR/launcher.py"
EOF
chmod +x ~/Desktop/ekagra.command
echo "Done. Double-click ekagra.command on your Desktop."
read -p "Press Enter to close"
