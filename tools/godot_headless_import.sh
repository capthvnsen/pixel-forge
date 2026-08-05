#!/usr/bin/env bash
# Headless import of every *.forge.json manifest under a directory, via the
# Pixel Asset Forge Godot plugin. See docs/godot.md.
#
# Usage: tools/godot_headless_import.sh [MANIFEST_DIR]
#   MANIFEST_DIR defaults to godot/forge (res://forge inside the sample project).
#   May be a res:// path or a plain filesystem path (e.g. tests/golden/fixtures/godot).
#
# Exits 0 with a SKIPPED notice if the `godot` binary is not installed, so CI without
# Godot does not fail. Exits non-zero if Godot IS installed and any manifest fails to
# validate/import.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GODOT_PROJECT_DIR="$REPO_ROOT/godot"
MANIFEST_DIR="${1:-$GODOT_PROJECT_DIR/forge}"

# Godot chdirs into --path before running the script, so a plain filesystem path (no
# res:// or user:// prefix) must be made absolute here or it resolves against the
# wrong directory. res://, user://, and already-absolute paths pass through untouched.
case "$MANIFEST_DIR" in
    res://*|user://*|/*) ;;
    *) MANIFEST_DIR="$(cd "$MANIFEST_DIR" && pwd)" ;;
esac

GODOT_BIN="${GODOT_BIN:-godot}"

if ! command -v "$GODOT_BIN" >/dev/null 2>&1; then
    echo "SKIPPED: '$GODOT_BIN' not found on PATH. Install Godot 4.4 to run the headless import (see docs/godot.md)."
    exit 0
fi

echo "Using $("$GODOT_BIN" --version) at $(command -v "$GODOT_BIN")"
echo "Manifest directory: $MANIFEST_DIR"

exec "$GODOT_BIN" --headless --path "$GODOT_PROJECT_DIR" \
    --script res://addons/pixel_asset_forge/headless_import.gd \
    -- --manifest-dir="$MANIFEST_DIR"
