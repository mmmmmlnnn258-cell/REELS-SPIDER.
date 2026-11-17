#!/usr/bin/env bash
# Railway entrypoint: migrate first, then run worker + dashboard against the same DB.
set -Eeuo pipefail

cd /app
mkdir -p "${DATA_DIR:-/app/data}"

echo "[startup] Applying SQLite migrations..."
/opt/worker-venv/bin/python /app/migrate.py

echo "[startup] Starting scheduler worker..."
/opt/worker-venv/bin/python -m worker.run &
WORKER_PID=$!

echo "[startup] Starting Next.js dashboard..."
cd /app/dashboard
npm start -- --hostname 0.0.0.0 --port "${PORT:-3000}" &
DASHBOARD_PID=$!

# If either process exits, stop the other and let Railway restart the service.
set +e
wait -n "$WORKER_PID" "$DASHBOARD_PID"
STATUS=$?
kill "$WORKER_PID" "$DASHBOARD_PID" 2>/dev/null
wait "$WORKER_PID" "$DASHBOARD_PID" 2>/dev/null
exit "$STATUS"
