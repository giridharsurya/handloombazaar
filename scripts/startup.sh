#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python scripts/run_migrations.py

PORT="${PORT:-8000}"
exec python -m uvicorn main:app --host 0.0.0.0 --port "$PORT"
