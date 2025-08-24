#!/bin/sh
set -e

MODE="${MODE:-api}"

case "$MODE" in
  api)
    exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
    ;;
  job)
    # Long-running job entrypoint
    exec uv run python job_main.py
    ;;
  *)
    # Fallback: execute passed command
    exec "$@"
    ;;
esac