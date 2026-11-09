#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec .venv/bin/kuku serve --config "${1:-config/default.json}" --port "${2:-8765}"
