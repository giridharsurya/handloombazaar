#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTHONPATH="$(pwd):${PYTHONPATH:-}"

PYTHON_BIN="${PYTHON_BIN:-$(find /tmp -path '*/antenv/bin/python' -type f 2>/dev/null | head -n 1)}"

if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="python"
fi

"$PYTHON_BIN" scripts/run_migrations.py

PORT="${PORT:-8000}"
exec "$PYTHON_BIN" -m uvicorn main:app --host 0.0.0.0 --port "$PORT"