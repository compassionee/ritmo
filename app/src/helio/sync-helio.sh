#!/bin/bash
# Copies a new version of Helio (the server folder) into helio-app/ and runs the tests.
#   app/src/helio/sync-helio.sh <Helio folder>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:?usage: sync-helio.sh <Helio folder>}"
for f in helio_app.py helio_science.py helio_transfer.py static/index.html static/app.js tests; do
  [ -e "$SRC/$f" ] || { echo "Missing $SRC/$f: this does not look like a Helio folder"; exit 1; }
done
# full copy: files removed from Helio disappear here too (git shows the differences)
rm -rf "$HERE/helio-app.new"
cp -a "$SRC" "$HERE/helio-app.new"
find "$HERE/helio-app.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$HERE/helio-app"
mv "$HERE/helio-app.new" "$HERE/helio-app"
git -C "$HERE" status --short -- helio-app
"$HERE/run-tests.sh"
