$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $scriptDir "..")

python scripts/run_migrations.py

$port = if ($env:PORT) { $env:PORT } else { "8000" }
python -m uvicorn main:app --host 0.0.0.0 --port $port
