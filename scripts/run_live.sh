#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
UV_BIN="${UV_BIN:-uv}"

cd "$REPO_ROOT"

if [[ "${1:-api}" != "api" ]]; then
    echo "usage: scripts/run_live.sh [api] [uvicorn arguments...]" >&2
    exit 2
fi
if [[ $# -gt 0 ]]; then
    shift
fi

if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    echo "required command is missing: uv" >&2
    exit 1
fi

"$UV_BIN" run --no-env-file --frozen python "$REPO_ROOT/scripts/preflight.py" \
    --profile api \
    --require-command "$UV_BIN"

exec "$UV_BIN" run --no-env-file --frozen uvicorn src.app.main:app --reload "$@"
