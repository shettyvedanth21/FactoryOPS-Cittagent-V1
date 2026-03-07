# Reporting Service

Production reporting engine for FactoryOPS.

This service generates **energy consumption reports** (JSON + PDF), stores report artifacts, exposes history/scheduling APIs, and provides centralized Settings APIs for tariff + alert recipients.

## 1. What This Service Does

- Generates energy reports from telemetry for single device or `ALL` devices.
- Applies robust fallback calculations when telemetry fields are missing.
- Computes quality level (`high|medium|low|insufficient`) with warnings.
- Fetches live tariff from settings storage at generation time.
- Stores report result JSON in DB and PDF in MinIO.
- Exposes report history, status, result, download, and schedule APIs.
- Hosts Settings APIs used by UI/rule engine:
  - alert notification recipients
  - tariff configuration

## 2. Core Calculation Model (Implemented)

Reporting logic is implemented in:
- `src/services/report_engine.py`
- `src/tasks/report_task.py`
- `src/services/insights_engine.py`

### 2.1 Priority order for total energy (`kWh`)

For each device, the engine applies the first valid method in this order:

1. **Priority 1: cumulative meter delta**
- Condition: `energy_kwh` available
- Formula:
  - `total_kwh = last(energy_kwh) - first(energy_kwh)`
- Method label: `energy_kwh_direct`
- Quality: `high`

2. **Priority 2: power integration**
- Condition: `power` available (kW)
- Formula:
  - `total_kwh = ∫ power_kw dt` (trapezoidal integration over time)
- Method label: `power_integration`
- Quality: `medium`

3. **Priority 3: derive power from V × I × PF, then integrate**
- Condition: `current`, `voltage`, and `power_factor` available
- Formula:
  - `derived_power_kw = (current * voltage * power_factor) / 1000`
  - `total_kwh = ∫ derived_power_kw dt`
- Method label: `derived_power_v_i_pf`
- Quality: `medium`

4. **Priority 4: PF missing, assume PF = 1.0, then integrate**
- Condition: `current` + `voltage` available, `power_factor` missing
- Formula:
  - `derived_power_kw = (current * voltage) / 1000`
  - `total_kwh = ∫ derived_power_kw dt`
- Method label: `derived_power_v_i_pf1`
- Quality: `low`
- Warning emitted: PF assumed 1.0

5. **Priority 5: current only or no usable fields**
- Condition: cannot compute power/energy reliably
- Result: no guess, explicit insufficient-data error
- Method labels:
  - `insufficient_current_only`
  - `insufficient_missing_fields`
  - `no_data`
- Quality: `insufficient`

### 2.2 Peak demand

- If `power` exists:
  - `peak_demand_kw = max(power)`
  - `peak_timestamp = timestamp at max(power)`
- Else if `current` + `voltage`:
  - `derived_kw = (current*voltage*pf)/1000` (or PF=1 if missing)
  - `peak_demand_kw = max(derived_kw)`

### 2.3 Average load and load factor

- `average_load_kw = total_kwh / total_hours`
- `load_factor_pct = (average_load_kw / peak_demand_kw) * 100`

Load factor bands:
- `< 30` → `poor`
- `30..70` → `moderate`
- `> 70` → `good`

### 2.4 Cost estimation (live tariff)

Tariff is fetched at report runtime from settings storage.

- `total_cost = total_kwh * tariff_rate`
- If tariff missing: cost is omitted with warning.

Stored in report result for auditability:
- `tariff_rate_used`
- `tariff_currency`
- `tariff_fetched_at`

### 2.5 Daily breakdown

Each day is recalculated using the same priority logic, returning per-day:
- `energy_kwh`
- `peak_demand_kw`
- `average_load_kw`
- `quality`
- `method`
- `warnings`

## 3. Smart vs Non-Smart Devices

Device behavior is governed by `data_source_type` from device-service:

- `metered` (non-smart / energy meter)
  - typically satisfies Priority 1 or 2
- `sensor` (smart / CT sensor)
  - typically satisfies Priority 2, 3, or 4

`phase_type` remains backward-compatible in device-service, but report computation is driven by actual telemetry field availability + `data_source_type` context.

## 4. Timezone Behavior

- Internal telemetry timestamps are handled in UTC.
- Report PDF display text is rendered in **IST**.
- Peak timestamp shown in PDF/insights uses IST label.

## 5. API Endpoints (Current)

Base URL: `http://localhost:8085`

### Health
- `GET /health`
- `GET /ready`

### Energy reports
- `POST /api/reports/energy/consumption`
  - body:
    - `device_id` (`"ALL"` or specific device id)
    - `start_date` (`YYYY-MM-DD`)
    - `end_date` (`YYYY-MM-DD`)
    - `tenant_id` (default `"default"`)
    - `report_name` (optional)
  - returns: `report_id`, `status`, `created_at`, `estimated_completion_seconds`

### Report common
- `GET /api/reports/history?tenant_id=...&limit=...&offset=...&report_type=...`
- `GET /api/reports/{report_id}/status?tenant_id=...`
- `GET /api/reports/{report_id}/result?tenant_id=...`
- `GET /api/reports/{report_id}/download?tenant_id=...`

### Schedule APIs
- `POST /api/reports/schedules?tenant_id=...`
- `GET /api/reports/schedules?tenant_id=...`
- `DELETE /api/reports/schedules/{schedule_id}?tenant_id=...`

### Settings APIs (centralized)
- `GET /api/v1/settings/tariff`
- `POST /api/v1/settings/tariff`
- `GET /api/v1/settings/notifications`
- `POST /api/v1/settings/notifications/email`
- `DELETE /api/v1/settings/notifications/email/{channel_id}`

### Legacy tariff API (still present)
- `POST /api/reports/tariffs`
- `GET /api/reports/tariffs/{tenant_id}`

### Legacy comparison API (still present)
- `POST /api/reports/energy/comparison`

> Note: UI flow is currently centered on energy consumption reporting.

## 6. Main Result JSON Shape (Consumption)

Returned by `GET /api/reports/{report_id}/result` when completed:

- `schema_version`
- `report_id`
- `start_date`, `end_date`
- `device_scope` (`ALL` or specific device)
- `summary`:
  - `total_kwh`, `peak_demand_kw`, `peak_timestamp`
  - `average_load_kw`, `load_factor_pct`, `load_factor_band`
  - `total_cost`, `currency`
- `data_quality`:
  - `overall`
  - `per_device.{device_id}.{quality, method, warnings, error}`
- `warnings`
- `insights`
- `daily_series`
- `devices` (per-device breakdown)
- `tariff_rate_used`, `tariff_currency`, `tariff_fetched_at`

## 7. Storage & Runtime

- Metadata and results: MySQL (`energy_reports`, schedules, settings tables)
- PDF artifacts: MinIO (`reports/{tenant}/{report_id}.pdf`)
- Telemetry reads: InfluxDB through `src/services/influx_reader.py`
- Device resolution/validation: device-service HTTP APIs

## 8. Operational Commands

### Run service stack
```bash
docker compose up -d --build reporting-service
```

### Migrations
```bash
docker compose exec reporting-service alembic upgrade head
```

### Quick health check
```bash
curl -s http://localhost:8085/health
curl -s http://localhost:8085/ready
```

### Smoke report submit
```bash
curl -X POST "http://localhost:8085/api/reports/energy/consumption" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "default",
    "device_id": "COMPRESSOR-001",
    "start_date": "2026-03-01",
    "end_date": "2026-03-07",
    "report_name": "Smoke Test"
  }'
```

## 9. Reliability Guarantees

- No silent guessing when insufficient fields are present.
- Explicit quality + warnings are always produced for fallback paths.
- Tariff is fetched at generation time (not hardcoded in report computation).
- Report jobs expose clear status/error via status endpoint.
