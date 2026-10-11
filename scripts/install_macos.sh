#!/usr/bin/env bash
# Install OpenFlow.app to /Applications and (optionally) auto-launch on login.
# Run from the openflow/ project root after `pyinstaller openflow.spec`.

set -euo pipefail

APP_SRC="${1:-dist/OpenFlow.app}"
APP_DEST="/Applications/OpenFlow.app"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/com.openflow.dictation.plist"

if [[ ! -d "$APP_SRC" ]]; then
    echo "error: $APP_SRC not found. Run 'pyinstaller openflow.spec' first." >&2
    exit 1
fi

echo "Installing to $APP_DEST..."
rm -rf "$APP_DEST"
cp -R "$APP_SRC" "$APP_DEST"
xattr -dr com.apple.quarantine "$APP_DEST" 2>/dev/null || true

read -r -p "Auto-launch OpenFlow on login? [y/N] " yn
if [[ "$yn" =~ ^[Yy]$ ]]; then
    # login_item.py writes the plists (the same ones Settings › Open at login
    # does): the login agent, which runs `open -a` so the app attaches to the
    # user's Aqua session, and the guard agent, which restarts OpenFlow if it
    # dies or hangs (never after a Quit). From source with the app installed,
    # both point at /Applications/OpenFlow.app.
    PY="${PYTHON:-.venv/bin/python}"
    [[ -x "$PY" ]] || PY="python3"
    env -u OPENFLOW_FROM_SOURCE "$PY" - <<'PYEOF'
import login_item
path = login_item.enable()
login_item.unload()            # an older copy, if loaded (fails quietly if not)
login_item.load(path)          # RunAtLoad: starts OpenFlow now
login_item.refresh_guard()     # loads the guard; it doesn't run at load
print(f"LaunchAgents installed at {path} and {login_item.guard_plist_path()}")
PYEOF
fi

cat <<EOF

Done. Next:

  1. Open System Settings -> Privacy & Security -> Accessibility
       and add /Applications/OpenFlow.app (toggle ON).
  2. The first record will prompt for Microphone access; allow it.
  3. Drop your API key in ~/.openflow/.env:
       echo 'SARVAM_API_KEY=...' > ~/.openflow/.env
  4. Launch OpenFlow.app from Spotlight (Cmd+Space, "OpenFlow").

Hold the configured record key (default right Option) to dictate.
EOF
