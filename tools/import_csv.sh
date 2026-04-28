#!/bin/bash
# Drop a fresh Ahrefs CSV into inbox/, commit, push. One command per refresh.
# Usage: ./tools/import_csv.sh /path/to/ahrefs-export.csv

set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <path-to-ahrefs-csv>"
  echo "Drops a fresh Ahrefs export into inbox/ and pushes."
  exit 1
fi

SRC="$1"
if [ ! -f "$SRC" ]; then
  echo "File not found: $SRC"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$REPO_ROOT/inbox/$(basename "$SRC")"

cp "$SRC" "$DEST"
cd "$REPO_ROOT"
git add "inbox/$(basename "$SRC")"
git commit -m "Update Ahrefs CSV: $(basename "$SRC")"
git push

echo
echo "✓ Imported $(basename "$SRC") and pushed."
echo "  Next scheduled posting run will pick it up."
