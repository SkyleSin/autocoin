#!/bin/sh
set -e
cd /app
alembic upgrade head
exec uvicorn trader.api.app:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
