#!/bin/sh
set -e
cd /app
exec uvicorn app:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
