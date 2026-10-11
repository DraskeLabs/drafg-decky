#!/usr/bin/env bash
# Build the plugin zip for Decky's "Install plugin from ZIP file".
#
# Layout Decky expects: one top-level folder named after the plugin, holding
# plugin.json, package.json, main.py, dist/index.js, py_modules/ and LICENSE.
# The zip carries the plugin's name too (DraFG.zip): Decky names a URL install
# after the file, and a different name can leave the old copy behind on a
# reinstall.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f dist/index.js ]] || { echo "dist/index.js missing: run 'pnpm run build' first" >&2; exit 1; }

NAME=DraFG
OUT=out
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/$NAME/dist" "$OUT"
cp plugin.json package.json main.py LICENSE THIRD_PARTY_NOTICES.md README.md PROTOCOL.md "$STAGE/$NAME/"
cp -r LICENSES "$STAGE/$NAME/"
cp dist/index.js "$STAGE/$NAME/dist/"
mkdir -p "$STAGE/$NAME/py_modules"
cp -r py_modules/drafg_decky "$STAGE/$NAME/py_modules/"
find "$STAGE" -name __pycache__ -prune -exec rm -rf {} +

rm -f "$OUT/$NAME.zip"
ZIP="$(pwd)/$OUT/$NAME.zip"
(cd "$STAGE" && python3 -m zipfile -c "$ZIP" "$NAME")
echo "wrote $OUT/$NAME.zip"
sha256sum "$ZIP" | sed "s|$(pwd)/||"
python3 -m zipfile -l "$ZIP"
