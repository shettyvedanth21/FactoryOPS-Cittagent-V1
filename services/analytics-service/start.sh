#!/bin/sh
set -e

echo "Running analytics-service migration guard..."
python scripts/migration_guard.py

echo "Applying analytics-service migrations..."
i=0
until alembic upgrade head; do
  i=$((i + 1))
  if [ "$i" -ge 20 ]; then
    echo "Failed to apply analytics-service migrations after $i attempts"
    exit 1
  fi
  echo "Migration attempt $i failed, retrying in 3s..."
  sleep 3
done

echo "Starting analytics-service..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8003
