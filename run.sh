#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8090}"
HOST="${HOST:-127.0.0.1}"
exec .venv/bin/uvicorn server.app:app --host "$HOST" --port "$PORT"
