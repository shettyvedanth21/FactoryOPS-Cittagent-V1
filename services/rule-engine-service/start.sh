#!/bin/sh
set -e

echo "Applying rule-engine migrations..."
i=0
until alembic upgrade head; do
  i=$((i + 1))
  if [ "$i" -ge 20 ]; then
    echo "Failed to apply rule-engine migrations after $i attempts"
    exit 1
  fi
  echo "Migration attempt $i failed, retrying in 3s..."
  sleep 3
done

echo "Starting rule-engine service..."
exec uvicorn app:app --host 0.0.0.0 --port 8002
