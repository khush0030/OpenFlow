#!/usr/bin/env bash
# Build OpenFlow.app then wrap it in a .dmg installer.
#
# Usage:
#   ./scripts/build_dmg.sh                 # build + dmg
#   SIGN_ID="Developer ID Application: ..." ./scripts/build_dmg.sh   # codesign
#   NOTARIZE=1 NOTARY_PROFILE=openflow-notary ./scripts/build_dmg.sh # notarize
#
# Requires:
#   - .venv with pyinstaller installed
#   - create-dmg in PATH (`brew install create-dmg`) for the .dmg step
#
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
    echo "OpenFlow ships macOS-only." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VENV="${VENV:-.venv}"
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "venv not found at $VENV — bootstrap with 'python -m venv .venv && .venv/bin/pip install -r requirements.txt'." >&2
    exit 1
fi

VERSION="$(grep -E '^version' pyproject.toml | head -1 | sed -E 's/version *= *"(.*)"/\1/')"
VERSION="${VERSION:-0.1.0}"

echo "==> Cleaning previous build"
rm -rf build dist

echo "==> Building PyInstaller bundle"
"$VENV/bin/pyinstaller" --noconfirm --clean openflow.spec

if [[ ! -d "dist/OpenFlow.app" ]]; then
    echo "PyInstaller did not produce dist/OpenFlow.app" >&2
    exit 1
fi

# Optional codesign
if [[ -n "${SIGN_ID:-}" ]]; then
    echo "==> Codesigning with: $SIGN_ID"
    codesign --force --options runtime --deep --sign "$SIGN_ID" \
        --entitlements entitlements.plist dist/OpenFlow.app
fi

# Optional notarize (xcrun notarytool, expects stored credentials profile)
if [[ "${NOTARIZE:-0}" == "1" ]]; then
    : "${NOTARY_PROFILE:?set NOTARY_PROFILE to the notarytool profile name}"
    ZIP="dist/OpenFlow-${VERSION}-notarize.zip"
    /usr/bin/ditto -c -k --keepParent dist/OpenFlow.app "$ZIP"
    xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple dist/OpenFlow.app
    rm -f "$ZIP"
fi

# DMG
DMG="dist/OpenFlow-${VERSION}.dmg"
rm -f "$DMG"

if command -v create-dmg >/dev/null 2>&1; then
    echo "==> Building DMG: $DMG"
    create-dmg \
        --volname "OpenFlow ${VERSION}" \
        --window-pos 200 120 \
        --window-size 640 360 \
        --icon-size 96 \
        --icon "OpenFlow.app" 160 180 \
        --hide-extension "OpenFlow.app" \
        --app-drop-link 480 180 \
        --no-internet-enable \
        "$DMG" "dist/OpenFlow.app"
    echo "==> Done: $DMG"
else
    echo "create-dmg not installed; skipping .dmg packaging."
    echo "  brew install create-dmg"
    echo "  Bundle is at dist/OpenFlow.app — drag it to /Applications manually."
fi
