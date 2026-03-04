# FactoryOPS API Reference (UI Team)

Last updated: 2026-03-04  
Source of truth: current backend route code in this repository.

## 1. Base URLs

### Internal service URLs (inside Docker network)
- Device Service: `http://device-service:8000`
- Data Service: `http://data-service:8081`
- Rule Engine Service: `http://rule-engine-service:8002`
- Analytics Service: `http://analytics-service:8003`
- Data Export Service: `http://data-export-service:8080`
- Reporting Service: `http://reporting-service:8085`

### Local host URLs (direct)
- Device: `http://localhost:8000`
- Data: `http://localhost:8081`
- Rule Engine: `http://localhost:8002`
- Analytics: `http://localhost:8003`
- Data Export: `http://localhost:8080`
- Reporting: `http://localhost:8085`

### UI proxy routes (from `ui-web/next.config.ts`)
- `/backend/device/*` -> Device Service
- `/backend/data/*` -> Data Service
- `/backend/rule-engine/*` -> Rule Engine Service
- `/backend/analytics/*` -> Analytics Service
- `/backend/data-export/*` -> Data Export Service
- `/api/reports/*` -> Reporting Service `/api/reports/*`

Use proxy routes from UI whenever possible.

## 2. Common Response Patterns

### Device/Rule/Data services
Usually envelope format:
```json
{
  "success": true,
  "data": {},
  "timestamp": "2026-03-04T12:00:00"
}
```
List endpoints may include pagination fields directly: `total`, `page`, `page_size`, `total_pages`.

### Analytics/Reporting/Data Export
Often raw JSON objects (not always wrapped in `success/data`).

## 3. Device Service API

Base path: `/api/v1/devices`

### Health
- `GET /health`
- `GET /ready`

### Devices
- `GET /api/v1/devices`
  - Query: `tenant_id`, `device_type`, `status`, `page` (default 1), `page_size` (default 20, max 100)
- `POST /api/v1/devices`
  - Body (`DeviceCreate`):
    - required: `device_id`, `device_name`, `device_type`
    - optional: `tenant_id`, `manufacturer`, `model`, `location`, `phase_type` (`single|three`), `metadata_json`
- `GET /api/v1/devices/{device_id}`
  - Query: `tenant_id`
- `PUT /api/v1/devices/{device_id}`
  - Query: `tenant_id`
  - Body (`DeviceUpdate` partial)
- `DELETE /api/v1/devices/{device_id}`
  - Query: `tenant_id`, `soft` (default `true`)

### Device property discovery
- `GET /api/v1/devices/properties`
  - Query: `tenant_id`
  - Returns all properties grouped by device plus `all_properties`
- `POST /api/v1/devices/properties/common`
  - Body: `{ "device_ids": ["D1", "D2"] }`
  - Returns intersection of common properties
- `GET /api/v1/devices/{device_id}/properties`
  - Query: `numeric_only` (default `true`)
- `POST /api/v1/devices/{device_id}/properties/sync`
  - Body: telemetry object; syncs discovered fields and updates last-seen
- `POST /api/v1/devices/{device_id}/heartbeat`
  - Updates `last_seen_timestamp` and runtime status

### Shift configuration
- `POST /api/v1/devices/{device_id}/shifts`
- `GET /api/v1/devices/{device_id}/shifts`
- `GET /api/v1/devices/{device_id}/shifts/{shift_id}`
- `PUT /api/v1/devices/{device_id}/shifts/{shift_id}`
- `DELETE /api/v1/devices/{device_id}/shifts/{shift_id}`
- `GET /api/v1/devices/{device_id}/uptime`

Shift body fields:
- `shift_name`, `shift_start` (`HH:MM:SS`), `shift_end` (`HH:MM:SS`)
- optional: `maintenance_break_minutes`, `day_of_week` (`0-6`), `is_active`

### Health score configuration
- `POST /api/v1/devices/{device_id}/health-config`
- `GET /api/v1/devices/{device_id}/health-config`
- `GET /api/v1/devices/{device_id}/health-config/{config_id}`
- `PUT /api/v1/devices/{device_id}/health-config/{config_id}`
- `DELETE /api/v1/devices/{device_id}/health-config/{config_id}`
- `GET /api/v1/devices/{device_id}/health-config/validate-weights`
- `POST /api/v1/devices/{device_id}/health-config/bulk`

Health config fields:
- `parameter_name`, `normal_min`, `normal_max`, `max_min`, `max_max`, `weight`, `ignore_zero_value`, `is_active`

### Health score calculation
- `POST /api/v1/devices/{device_id}/health-score`
  - Body:
```json
{
  "values": {
    "temperature": 45.2,
    "pressure": 5.1
  },
  "machine_state": "RUNNING"
}
```

## 4. Data Service API

Base path: `/api/v1/data`

### Service and health
- `GET /`
- `GET /api/v1/data/health`

### Telemetry queries
- `GET /api/v1/data/telemetry/{device_id}`
  - Query: `start_time`, `end_time`, `fields` (comma-separated), `aggregate`, `interval`, `limit` (1-10000)
- `GET /api/v1/data/stats/{device_id}`
  - Query: `start_time`, `end_time`
- `POST /api/v1/data/query`
  - Body (`TelemetryQuery`):
    - `device_id` (required)
    - optional: `start_time`, `end_time`, `fields`, `aggregate`, `interval`, `limit`

### WebSocket
- `WS /ws/telemetry/{device_id}`
  - Server messages: `connected`, `telemetry`, `heartbeat`
  - Client messages supported: `{"type":"ping"}`, `{"type":"subscribe"}`
- `GET /ws/stats`

### Telemetry payload contract (ingestion)
Published to MQTT topic: `devices/{device_id}/telemetry`

Required fields:
- `device_id` (string)
- `timestamp` (ISO-8601 UTC)
- `schema_version` (string, usually `v1`)

Any additional numeric fields are stored and can be used in rules and charts.

## 5. Rule Engine Service API

Base path: `/api/v1`

### Health
- `GET /health`
- `GET /ready`

### Rules
- `GET /api/v1/rules`
  - Query: `tenant_id`, `status` (`active|paused|archived`), `device_id`, `page`, `page_size`
- `POST /api/v1/rules`
- `GET /api/v1/rules/{rule_id}`
- `PUT /api/v1/rules/{rule_id}`
- `PATCH /api/v1/rules/{rule_id}/status`
  - Body: `{ "status": "active|paused|archived" }`
- `DELETE /api/v1/rules/{rule_id}`
  - Query: `tenant_id`, `soft` (default `true`)

Rule create/update fields:
- `rule_name` (required)
- `description` (optional)
- `scope`: `all_devices | selected_devices`
- `device_ids`: `string[]` (used for `selected_devices`)
- `property` (telemetry field name, required)
- `condition`: `>`, `<`, `=`, `!=`, `>=`, `<=`
- `threshold` (number)
- `notification_channels`: `email|whatsapp|telegram` array
- `cooldown_minutes` (0-1440)
- optional `tenant_id`

### Rule evaluation (called by data-service)
- `POST /api/v1/rules/evaluate`
  - Body: telemetry payload with dynamic numeric fields
  - Returns counts and evaluation results

### Alerts
- `GET /api/v1/alerts`
  - Query: `tenant_id`, `device_id`, `rule_id`, `status`, `page`, `page_size`
- `PATCH /api/v1/alerts/{alert_id}/acknowledge`
  - Body: `{ "acknowledged_by": "user@company.com" }`
- `PATCH /api/v1/alerts/{alert_id}/resolve`

## 6. Analytics Service API

Base path: `/api/v1/analytics`

### Health
- `GET /health/live`
- `GET /health/ready`

### Jobs
- `POST /api/v1/analytics/run`
  - Async submit, returns `job_id`
  - Body (`AnalyticsRequest`):
    - required: `device_id`, `analysis_type` (`anomaly|prediction|forecast`), `model_name`
    - one of:
      - `dataset_key`
      - OR `start_time` + `end_time`
    - optional: `parameters` object
- `GET /api/v1/analytics/status/{job_id}`
- `GET /api/v1/analytics/results/{job_id}`
- `GET /api/v1/analytics/jobs`
  - Query: `status`, `device_id`, `limit`, `offset`

### Metadata
- `GET /api/v1/analytics/models`
- `GET /api/v1/analytics/datasets?device_id={device_id}`

## 7. Data Export Service API

### Health
- `GET /health`
- `GET /ready`

### Export control
- `POST /api/v1/exports/run`
  - Body: `{ "device_id": "COMPRESSOR-001" }`
  - `device_id` optional; if omitted, exporter runs broadly per worker logic
- `GET /api/v1/exports/status/{device_id}`

## 8. Reporting Service API

Base path: `/api/reports`

### Health
- `GET /health`
- `GET /ready`

### Energy consumption reports
- `POST /api/reports/energy/consumption`
  - Body (`ConsumptionReportRequest`):
```json
{
  "tenant_id": "tenant1",
  "device_ids": ["COMPRESSOR-001"],
  "start_date": "2026-03-01",
  "end_date": "2026-03-03",
  "group_by": "daily"
}
```
  - Note: `device_ids` may include `"all"`

### Comparison reports
- `POST /api/reports/energy/comparison`
- `POST /api/reports/energy/comparison/`

Body (`ComparisonReportRequest`):
- `comparison_type = machine_vs_machine`
  - required: `tenant_id`, `machine_a_id`, `machine_b_id`, `start_date`, `end_date`
- `comparison_type = period_vs_period`
  - required: `tenant_id`, `device_id`, `period_a_start`, `period_a_end`, `period_b_start`, `period_b_end`

### Tariffs
- `POST /api/reports/tariffs/`
  - Body: `tenant_id`, `energy_rate_per_kwh`, `demand_charge_per_kw`, `reactive_penalty_rate`, `fixed_monthly_charge`, `power_factor_threshold`, `currency`
- `GET /api/reports/tariffs/{tenant_id}`

### Report history/status/download
- `GET /api/reports/history?tenant_id={tenant_id}&limit=20&offset=0&report_type={optional}`
- `GET /api/reports/{report_id}/status?tenant_id={tenant_id}`
- `GET /api/reports/{report_id}/result?tenant_id={tenant_id}`
- `GET /api/reports/{report_id}/download?tenant_id={tenant_id}`

### Schedules
- `POST /api/reports/schedules?tenant_id={tenant_id}`
  - Body: schedule payload (`report_type`, `frequency`, `params_template`, etc.)
- `GET /api/reports/schedules?tenant_id={tenant_id}`
- `DELETE /api/reports/schedules/{schedule_id}?tenant_id={tenant_id}`

## 9. UI Integration Notes

- Use `tenant_id` consistently for reporting and multi-tenant aware endpoints.
- Device `runtime_status` is computed from telemetry activity (`running/stopped`), not a static DB field.
- For rules UI property dropdowns:
  - all devices: `GET /api/v1/devices/properties`
  - selected devices common fields: `POST /api/v1/devices/properties/common`
- For real-time telemetry widgets, use `WS /ws/telemetry/{device_id}`.
- Reporting APIs are proxied as `/api/reports/*` in UI, while other services are under `/backend/*`.

## 10. Quick Test Commands

### List devices
```bash
curl -s "http://localhost:8000/api/v1/devices"
```

### Create rule
```bash
curl -X POST "http://localhost:8002/api/v1/rules" \
  -H "Content-Type: application/json" \
  -d '{
    "rule_name": "High Temperature",
    "scope": "selected_devices",
    "device_ids": ["COMPRESSOR-001"],
    "property": "temperature",
    "condition": ">",
    "threshold": 60,
    "notification_channels": ["email"],
    "cooldown_minutes": 15
  }'
```

### Query telemetry
```bash
curl -s "http://localhost:8081/api/v1/data/telemetry/COMPRESSOR-001?limit=20"
```
