#!/usr/bin/env bash
# One-time setup: create a stable self-signed code-signing identity in the
# user's login keychain so PyInstaller rebuilds produce a consistent binary
# signature. That keeps macOS Accessibility + Microphone grants persistent
# across rebuilds instead of triggering a fresh permission prompt every time.
#
# Usage:
#   ./scripts/setup_codesign.sh
#
# After running, openflow.spec uses codesign_identity="OpenFlow Local Dev"
# and rebuilds reuse the SAME signature.
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "macOS only." >&2
  exit 1
fi

IDENTITY="OpenFlow Local Dev"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-identity -v -p codesigning "$KEYCHAIN" | grep -q "$IDENTITY"; then
  echo "✓ Identity '$IDENTITY' already exists. Nothing to do."
  exit 0
fi

echo "==> Generating self-signed code-signing certificate '$IDENTITY'"
TMPDIR="$(mktemp -d)"
trap "rm -rf $TMPDIR" EXIT
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

echo "==> Importing into login keychain"
security import identity.p12 -k "$KEYCHAIN" -P openflow \
  -T /usr/bin/codesign -T /usr/bin/security >/dev/null

echo "==> Granting codesign access to private key (may prompt for keychain password)"
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "" "$KEYCHAIN" >/dev/null 2>&1 || {
  echo
  echo "  ! set-key-partition-list needs your keychain password."
  echo "  Run this manually if the cert prompts on every build:"
  echo "    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k '<keychain-pw>' $KEYCHAIN"
}

echo
echo "✓ Identity created. Next 'pyinstaller openflow.spec' build will be signed"
echo "  with '$IDENTITY' and reuse the same TCC grants."
