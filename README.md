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

### Step 3: Migrations run on startup (mandatory policy)
`device-service`, `analytics-service`, `rule-engine-service`, `reporting-service`, and
`waste-analysis-service` run Alembic migrations on container startup.

Migration policy:
- Schema is Alembic-managed (no runtime `create_all`).
- Startup uses a guarded migration preflight.
- Alembic stamping is allowed only when schema matches the expected baseline shape.
- Drifted schemas fail fast with actionable logs.

### Step 4: Health verification
```bash
docker compose ps
curl -s http://localhost:8000/health
curl -s http://localhost:8081/api/v1/data/health
curl -s http://localhost:8002/health
curl -s http://localhost:8085/health
```

UI should be available at: `http://localhost:3000`

### UI runtime guardrail verification (Machines route)
Use this after UI changes before sign-off:

```bash
cd ui-web
npm run lint:hooks
npm run build
```

Runtime smoke check:
- Open `http://localhost:3000/machines/COMPRESSOR-001` (configured machine)
- Open `http://localhost:3000/machines/COMPRESSOR-003` (unconfigured/new machine)
- Expected: no client-side crash (`React #310`), and route-level fallback appears only on unexpected runtime errors.

### Runtime endpoint matrix (compose defaults)

| Component | URL |
|---|---|
| UI | `http://localhost:3000` |
| Device Service | `http://localhost:8000` |
| Rule Engine Service | `http://localhost:8002` |
| Analytics Service | `http://localhost:8003` |
| Data Export Service | `http://localhost:8080` |
| Data Service | `http://localhost:8081` |
| Reporting Service | `http://localhost:8085` |
| Waste Analysis Service | `http://localhost:8087` |

---

## 2. Device Onboarding (UI-visible)

Create devices in Device Service using `data_source_type`:
- `metered` = non-smart / energy meter source
- `sensor` = smart / CT sensor source

### 2.1 Non-smart device (metered)

```bash
curl -X POST "http://localhost:8000/api/v1/devices" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "COMPRESSOR-001",
    "device_name": "Compressor 001",
    "device_type": "compressor",
    "data_source_type": "metered",
    "phase_type": "three",
    "manufacturer": "Atlas Copco",
    "model": "GA37",
    "location": "Plant A",
    "metadata_json": "{\"floor\":\"1\",\"line\":\"A\"}"
  }'
```

### 2.2 Smart device (sensor)

```bash
curl -X POST "http://localhost:8000/api/v1/devices" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "COMPRESSOR-002",
    "device_name": "Compressor 002",
    "device_type": "compressor",
    "data_source_type": "sensor",
    "phase_type": "three",
    "manufacturer": "Atlas Copco",
    "model": "GA37",
    "location": "Plant B",
    "metadata_json": "{\"floor\":\"2\",\"line\":\"B\"}"
  }'
```

`phase_type` is still accepted for backward compatibility, but reporting logic is driven by `data_source_type`.

Verify onboarding:
```bash
curl -s "http://localhost:8000/api/v1/devices/COMPRESSOR-001"
```

Note: runtime status changes to `running` only after telemetry arrives.

### 2.3 Dashboard Widget Configuration (per-device)

Machines Overview now renders only the telemetry widgets configured for that device.

- Config ownership: per-device shared config in `device-service`.
- Scope: Overview KPI cards + Overview trend charts.
- Processing contract: telemetry ingestion, analytics, health scoring, and rules still process all telemetry fields.
- Default behavior: if no saved widget config exists, all discovered numeric telemetry fields are shown.

Endpoints:

```bash
# Read widget configuration
curl -s "http://localhost:8000/api/v1/devices/COMPRESSOR-001/dashboard-widgets"

# Replace selected fields (idempotent full replace)
curl -X PUT "http://localhost:8000/api/v1/devices/COMPRESSOR-001/dashboard-widgets" \
  -H "Content-Type: application/json" \
  -d '{"selected_fields":["current","power"]}'
```

Response shape includes:
- `available_fields`: discovered numeric telemetry fields
- `selected_fields`: persisted configured fields
- `effective_fields`: fields the UI should render
- `default_applied`: `true` when fallback-all is active (no explicit config)

### 2.4 Shift Overlap Policy (production behavior)

Shift scheduling is now server-enforced and non-overlapping per device.

- Overlaps are blocked for all shifts (active and inactive).
- Boundary-touching is allowed (end-exclusive): `09:00-10:00` + `10:00-11:00` is valid.
- Works with:
  - `day_of_week=null` (all days),
  - day-specific shifts,
  - cross-midnight windows (for example `22:00-02:00`).
- On conflict, shift create/update returns HTTP `409` with conflict details.

Legacy cleanup:
- A one-time migration removes only **exact duplicate** shift rows
  (`device_id`, `tenant_id`, `day_of_week`, `shift_start`, `shift_end`),
  keeping the oldest row and deleting newer duplicates.

Operational follow-up check:
```bash
./scripts/report_shift_overlap_conflicts.sh
```
This reports any remaining non-exact overlap conflicts for manual cleanup.

---

## 3. Start Simulator for an Onboarded Device

Run simulator for a specific device:

```bash
cd /home/ubuntu/FactoryOPS-Testing
DEVICE_ID=COMPRESSOR-001 docker compose --profile demo up -d telemetry-simulator
docker compose logs -f telemetry-simulator

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

## 4. Firmware Team Integration (Telemetry Contract — Authoritative)

This section is the source-of-truth contract for firmware publishing telemetry to FactoryOPS.

### 4.1 MQTT connection
- Broker host: `localhost` (or deployment host)
- Port: `1883`
- Topic: `devices/{device_id}/telemetry`
- QoS: `1` recommended

Example topic:
- `devices/COMPRESSOR-001/telemetry`

### 4.2 Required JSON fields
- `device_id` (string; must exactly match onboarded `device_id`)
- `timestamp` (ISO8601 UTC, e.g. `2026-03-04T12:00:00Z`)
- `schema_version` (use `"v1"`)

All measurement values must be numeric JSON values (no unit-suffixed strings like `"230V"`).

### 4.3 Canonical field names and units
- `voltage`: Volts (`V`)
- `current`: Amperes (`A`)
- `power`: Watts (`W`)  <-- important
- `power_factor`: ratio (`0.0` to `1.0`)
- `energy_kwh`: cumulative energy (`kWh`, monotonic increasing preferred)

### 4.4 Accepted aliases (backward-compatible)
- Current aliases: `current_l1`, `current_l2`, `current_l3`, `phase_current`, `i_l1`
- Voltage aliases: `voltage_l1`, `voltage_l2`, `voltage_l3`, `v_l1`
- Power-factor alias: `pf`
- Power aliases: `active_power`, `kw`

Unit handling rule:
- `power` and `active_power` are interpreted as `W` and normalized to `kW` internally.
- `kw` is interpreted as `kW` directly.

### 4.5 Processing priority (how backend computes energy)
1. Use `energy_kwh` delta when available.
2. Else integrate normalized power over time.
3. Else derive from `voltage * current * power_factor`.
4. If PF missing, backend may use PF=`1.0` with lower quality classification.
5. If required fields are missing, quality degrades and strict flows can fail.

### 4.6 Payload examples
```json
{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-04T12:00:00Z",
  "schema_version": "v1",
  "voltage": 231.4,
  "current": 0.86,
  "power": 198.7,
  "power_factor": 0.98,
  "energy_kwh": 1245.337,
  "temperature": 45.9,
  "pressure": 5.2
}
```
## Firmware team Strict follow.

## Required payload format (ingest-safe)
- `device_id`: string, must exactly match onboarded ID (example: `COMPRESSOR-003`)
- `timestamp`: ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`)
- Numeric fields must be **numbers only** (no `"230V"` style strings)

Recommended topic pattern:
- `devices/{device_id}/telemetry`

## Units and field names (canonical)

- `voltage`: **Volts (V)**
- `current`: **Amps (A)**
- `power`: **Watts (W)**  ← important
- `power_factor`: ratio **0.0 to 1.0**
- `energy_kwh`: cumulative **kWh** meter reading (monotonic increasing if possible)

## Optional aliases currently accepted
- Current: `current_l1`, `current_l2`, `current_l3`, `phase_current`, `i_l1`
- Voltage: `voltage_l1`, `voltage_l2`, `voltage_l3`, `v_l1`
- PF: `pf`
- Power: `active_power`, `kw` (if `kw`, it is treated as kW directly)

## Calculation priority (what backend expects)
1. `energy_kwh` delta (best)
2. Integrate `power` over time (expects `power` in **W**, backend normalizes to kW)
3. Derive from `voltage * current * power_factor`
4. If PF missing, assumes PF=1.0 (lower quality)
5. If only current or missing required fields, quality drops / report can fail in strict mode

## Critical operational rules
- Keep timestamps strictly increasing (avoid duplicate/out-of-order points)
- Send stable cadence (e.g., every 5s/10s; backend aggregates to window)
- For waste analysis strict mode, idle threshold must be configured per device in UI
- Time is ingested in UTC; UI/PDF may display IST

## Reference payload example
```json
{
  "device_id": "COMPRESSOR-003",
  "timestamp": "2026-03-07T12:30:00Z",
  "voltage": 230.2,
  "current": 0.92,
  "power": 211.8,
  "power_factor": 0.98,
  "energy_kwh": 1245.337
}
```

If your firmware team follows this exactly, analytics/reports/waste stay consistent and won’t break.

Any additional numeric fields are auto-ingested and usable in rules.

### 4.7 Firmware reliability rules (must follow)
- Keep timestamps strictly increasing for each `device_id`.
- Avoid duplicate timestamps in high-frequency bursts.
- Keep a stable publish cadence (for example every `5s`, `10s`, or `30s`).
- Always publish UTC timestamps; UI/PDF may display IST.

### 4.8 CLI publish test
```bash
mosquitto_pub -h localhost -p 1883 -t devices/COMPRESSOR-001/telemetry -m '{
  "device_id": "COMPRESSOR-001",
  "timestamp": "2026-03-04T12:00:00Z",
  "schema_version": "v1",
  "voltage": 230.8,
  "current": 0.88,
  "power": 203.0,
  "power_factor": 0.98
}'
```

### 4.9 Verify telemetry ingestion
```bash
curl -s "http://localhost:8081/api/v1/data/telemetry/COMPRESSOR-001?limit=10"
```

---

## 5. Production Safety Notes

- Do not run `docker compose down -v` in production (removes named volumes).
- Keep `.env` values consistent across restarts/deployments.
- For email alerts in rules, configure SMTP in `.env` (`EMAIL_*` or `SMTP_*` mapping used by rule engine).
