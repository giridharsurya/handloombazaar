#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTHONPATH="$(pwd):${PYTHONPATH:-}"

PYTHON_BIN="${PYTHON_BIN:-python}"

PORT="${PORT:-8000}"
exec "$PYTHON_BIN" -m uvicorn main:app --host 0.0.0.0 --port "$PORT"