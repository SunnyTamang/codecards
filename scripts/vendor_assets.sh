#!/usr/bin/env bash
# Fetch and pin the browser libraries. Run from the repo root.
# elkjs is the only one: everything else is hand-written, because cards must
# be DOM elements and a canvas-drawing graph library cannot host DOM.
set -euo pipefail

ELKJS_VERSION=0.12.0

DEST="src/codecards/render/assets/vendor"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$DEST"
cd "$WORK"
npm pack "elkjs@${ELKJS_VERSION}" >/dev/null
tar xzf "elkjs-${ELKJS_VERSION}.tgz"
cd - >/dev/null

cp "$WORK/package/lib/elk.bundled.js" "$DEST/"

cat > "$DEST/VERSIONS.txt" <<EOF
elkjs ${ELKJS_VERSION}   lib/elk.bundled.js
EOF

echo "vendored into $DEST:"
ls -la "$DEST"
