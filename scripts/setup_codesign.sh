#!/usr/bin/env bash
# One-time setup: create a stable self-signed code-signing identity so
# PyInstaller rebuilds produce a consistent signature. That keeps macOS
# Accessibility + Microphone grants persistent across rebuilds instead of
# triggering a fresh permission prompt every time.
#
# The identity lives in a dedicated keychain whose password is stored at
# ~/.openflow/.build-keychain-pass (chmod 600), so scripts/build_app.sh can
# unlock it headlessly after a reboot.
#
# Usage:
#   ./scripts/setup_codesign.sh
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "macOS only." >&2
  exit 1
fi

IDENTITY="OpenFlow Local Dev"
KEYCHAIN="$HOME/Library/Keychains/openflow-signing.keychain-db"
PASS_FILE="$HOME/.openflow/.build-keychain-pass"

if [[ -f "$KEYCHAIN" && -f "$PASS_FILE" ]]; then
  security unlock-keychain -p "$(cat "$PASS_FILE")" "$KEYCHAIN"
  if security find-identity -v -p codesigning "$KEYCHAIN" | grep -q "$IDENTITY"; then
    echo "✓ Identity '$IDENTITY' already exists in $KEYCHAIN. Nothing to do."
    exit 0
  fi
fi

mkdir -p "$(dirname "$PASS_FILE")"
if [[ ! -f "$PASS_FILE" ]]; then
  (umask 077; openssl rand -hex 24 > "$PASS_FILE")
fi
PASS="$(cat "$PASS_FILE")"

echo "==> Creating keychain $KEYCHAIN"
[[ -f "$KEYCHAIN" ]] || security create-keychain -p "$PASS" "$KEYCHAIN"
security set-keychain-settings "$KEYCHAIN"   # no auto-lock timeout
security unlock-keychain -p "$PASS" "$KEYCHAIN"

# Put it in the user search list (ahead of login) so codesign finds it.
existing=$(security list-keychains -d user | tr -d '"' | grep -v "$KEYCHAIN" || true)
# shellcheck disable=SC2086
security list-keychains -d user -s "$KEYCHAIN" $existing

echo "==> Generating self-signed code-signing certificate '$IDENTITY'"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
cd "$TMPDIR"

cat > req.cnf <<'EOF'
[ req ]
distinguished_name = dn
prompt = no
req_extensions = v3
x509_extensions = v3
[ dn ]
CN = OpenFlow Local Dev
O = OpenFlow
[ v3 ]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF

openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 \
  -nodes -config req.cnf -extensions v3 >/dev/null 2>&1

openssl pkcs12 -legacy -export -inkey key.pem -in cert.pem \
  -out identity.p12 -password pass:openflow -name "$IDENTITY" >/dev/null

security import identity.p12 -k "$KEYCHAIN" -P openflow \
  -T /usr/bin/codesign -T /usr/bin/security >/dev/null
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$PASS" "$KEYCHAIN" >/dev/null
# Self-signed certs only count as codesigning identities once trusted.
security add-trusted-cert -p codeSign -k "$KEYCHAIN" cert.pem

echo
echo "✓ Identity created. Build with ./scripts/build_app.sh"
