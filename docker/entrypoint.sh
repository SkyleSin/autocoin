#!/bin/sh
set -e
cd /app
if [ $# -gt 0 ]; then
    exec "$@"
fi
exec uvicorn app:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
