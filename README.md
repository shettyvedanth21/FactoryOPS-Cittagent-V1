# FactoryOPS / Cittagent Platform

Production setup and operations guide for first-time deployment, migrations, device onboarding, and firmware telemetry integration.

## 1. First-Time Setup (Correct Order)

### Prerequisites
- Docker + Docker Compose v2
- Port availability: `3000, 8000, 8002, 8003, 8080, 8081, 8085, 8086, 9000, 9001, 1883, 3306`

### Step 1: Configure environment
```bash
cp .env.example .env
```
Edit `.env` with production values (especially email and secrets).

### Step 2: Start platform
```bash
docker compose up -d --build
```

This stack includes persistent named volumes:
- `mysql_data`
- `influxdb_data`
- `minio_data`

Do not remove these volumes in production unless you intentionally want data loss.

### Step 3: Run DB migrations (mandatory)
`rule-engine-service` and `reporting-service` auto-run migrations on startup.
`device-service` requires explicit migration command:

```bash
docker compose exec device-service alembic upgrade head
```

Optional manual re-run (safe if already applied):
```bash
docker compose exec rule-engine-service alembic upgrade head
docker compose exec reporting-service alembic upgrade head
```

### Step 4: Health verification
```bash
docker compose ps
curl -s http://localhost:8000/health
curl -s http://localhost:8081/api/v1/data/health
curl -s http://localhost:8002/health
curl -s http://localhost:8085/health
```

UI should be available at: `http://localhost:3000`

---

## 2. Device Onboarding (UI-visible)

Create a device in Device Service:

```bash
curl -X POST "http://localhost:8000/api/v1/devices" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "COMPRESSOR-001",
    "device_name": "Compressor 001",
    "device_type": "compressor",
    "phase_type": "three",
    "manufacturer": "Atlas Copco",
    "model": "GA37",
    "location": "Plant A",
    "metadata_json": "{\"floor\":\"1\",\"line\":\"A\"}"
  }'
```

Verify onboarding:
```bash
curl -s "http://localhost:8000/api/v1/devices/COMPRESSOR-001"
```

Note: runtime status changes to `running` only after telemetry arrives.

---

## 3. Start Simulator for an Onboarded Device

Run simulator for a specific device:

```bash
DEVICE_ID=COMPRESSOR-001 docker compose --profile demo up -d telemetry-simulator
```

Check simulator logs:
```bash
docker compose logs -f telemetry-simulator
```

Stop simulator:
```bash
docker compose --profile demo stop telemetry-simulator
```

---

## 4. Firmware Team Integration (Sensor Data Contract)

### MQTT connection
- Broker host: `localhost` (or deployment host)
- Port: `1883`
- Topic: `devices/{device_id}/telemetry`
- QoS: `1` recommended

Example topic:
- `devices/COMPRESSOR-001/telemetry`

### Required JSON fields
- `device_id` (string)
- `timestamp` (ISO8601 UTC, e.g. `2026-03-04T12:00:00Z`)
- `schema_version` (use `"v1"`)

### Telemetry payload example
```json
{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-04T12:00:00Z",
  "schema_version": "v1",
  "voltage": 231.4,
  "current": 0.86,
  "power": 198.7,
  "temperature": 45.9,
  "pressure": 5.2
}
```

Any additional numeric fields are auto-ingested and usable in rules.

### CLI publish test
```bash
mosquitto_pub -h localhost -p 1883 -t devices/COMPRESSOR-001/telemetry -m '{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-04T12:00:00Z",
  "schema_version": "v1",
  "temperature": 46.1,
  "pressure": 5.3,
  "power": 201.2
}'
```

### Verify telemetry ingestion
```bash
curl -s "http://localhost:8081/api/v1/data/telemetry/COMPRESSOR-001?limit=10"
```

---

## 5. Production Safety Notes

- Do not run `docker compose down -v` in production (removes named volumes).
- Keep `.env` values consistent across restarts/deployments.
- For email alerts in rules, configure SMTP in `.env` (`EMAIL_*` or `SMTP_*` mapping used by rule engine).

