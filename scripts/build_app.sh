#!/usr/bin/env bash
# Build, sign, and (with --deploy) install OpenFlow.app, then restart it.
#
# Usage:
#   ./scripts/build_app.sh            # build dist/OpenFlow.app
#   ./scripts/build_app.sh --deploy   # build + replace /Applications/OpenFlow.app + relaunch
set -euo pipefail

cd "$(dirname "$0")/.."

KEYCHAIN="$HOME/Library/Keychains/openflow-signing.keychain-db"
PASS_FILE="$HOME/.openflow/.build-keychain-pass"
APP_DEST="/Applications/OpenFlow.app"
PLIST="$HOME/Library/LaunchAgents/com.openflow.dictation.plist"

if [[ ! -f "$KEYCHAIN" || ! -f "$PASS_FILE" ]]; then
  echo "No signing keychain — run ./scripts/setup_codesign.sh first." >&2
  exit 1
fi
security unlock-keychain -p "$(cat "$PASS_FILE")" "$KEYCHAIN"

rm -rf build dist
.venv/bin/pyinstaller --noconfirm openflow.spec
# PyInstaller signs nested .frameworks (Python, Qt) without their resource
# seal, which fails --strict verification. Re-sign each framework's version
# dir inside-out, then re-seal the outer bundle with entitlements.
while IFS= read -r fw; do
  v=$(find "$fw/Versions" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)
  codesign --force --options runtime --timestamp -s "OpenFlow Local Dev" "${v:-$fw}" >/dev/null
done < <(find dist/OpenFlow.app -name '*.framework' -type d -prune)
codesign --force --options runtime --timestamp --entitlements entitlements.plist \
  -s "OpenFlow Local Dev" dist/OpenFlow.app
codesign --verify --deep --strict dist/OpenFlow.app
echo "✓ Built and verified dist/OpenFlow.app"

if [[ "${1:-}" == "--deploy" ]]; then
  pkill -f "OpenFlow.app/Contents/MacOS/openflow" 2>/dev/null || true
  sleep 1
  rm -rf "$APP_DEST"
  cp -R dist/OpenFlow.app "$APP_DEST"
  xattr -dr com.apple.quarantine "$APP_DEST" 2>/dev/null || true
  if [[ -f "$PLIST" ]]; then
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || open -a "$APP_DEST"
  else
    open -a "$APP_DEST"
  fi
  echo "✓ Deployed to $APP_DEST and relaunched"
fi
