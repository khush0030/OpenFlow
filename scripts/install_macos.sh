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
    mkdir -p "$PLIST_DIR"
    # Launch via `open` so the app attaches to the user's GUI/Aqua session.
    # Cocoa / pystray refuse to init outside an Aqua session and the daemon
    # SIGABRTs as soon as the icon goes up. `open -a` hands the bundle to
    # LaunchServices, which puts it in the right session context.
    cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.openflow.dictation</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>/Applications/OpenFlow.app</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
    <key>StandardOutPath</key><string>/tmp/openflow.out.log</string>
    <key>StandardErrorPath</key><string>/tmp/openflow.err.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load "$PLIST_PATH"
    echo "LaunchAgent installed at $PLIST_PATH"
fi

cat <<EOF

Done. Next:

  1. Open System Settings -> Privacy & Security -> Accessibility
       and add /Applications/OpenFlow.app (toggle ON).
  2. The first record will prompt for Microphone access; allow it.
  3. Drop your API key in ~/.openflow/.env:
       echo 'ANTHROPIC_API_KEY=sk-ant-...' > ~/.openflow/.env
  4. Launch OpenFlow.app from Spotlight (Cmd+Space, "OpenFlow").

Hold the right Option key to dictate.
EOF
