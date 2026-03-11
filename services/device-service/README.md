# Device Service

Device metadata, configuration, health scoring, shifts, runtime trends, and idle-running APIs for FactoryOPS.

## Base URL
- `http://<host>:8000`
- API prefix: `/api/v1`

## Health Endpoints
- `GET /health`
- `GET /ready`

## Device APIs (`/api/v1/devices`)

### Core
- `GET /api/v1/devices`
- `POST /api/v1/devices`
- `GET /api/v1/devices/{device_id}`
- `PUT /api/v1/devices/{device_id}`
- `DELETE /api/v1/devices/{device_id}`

### Dashboard / Properties
- `GET /api/v1/devices/dashboard/summary`
- `GET /api/v1/devices/properties`
- `POST /api/v1/devices/properties/common`
- `GET /api/v1/devices/{device_id}/properties`
- `POST /api/v1/devices/{device_id}/properties/sync`
- `GET /api/v1/devices/{device_id}/dashboard-widgets`
- `PUT /api/v1/devices/{device_id}/dashboard-widgets`

### Shifts
- `POST /api/v1/devices/{device_id}/shifts`
- `GET /api/v1/devices/{device_id}/shifts`
- `GET /api/v1/devices/{device_id}/shifts/{shift_id}`
- `PUT /api/v1/devices/{device_id}/shifts/{shift_id}`
- `DELETE /api/v1/devices/{device_id}/shifts/{shift_id}`

Shift conflict behavior:
- `POST` / `PUT` can return `409` when the candidate shift overlaps existing device shifts.
- Touching boundaries are allowed (`end` is exclusive).
- Validation includes all-days, day-specific, and cross-midnight shifts.
- Rollout hygiene check (repo root): `./scripts/report_shift_overlap_conflicts.sh`

### Uptime / Performance
- `GET /api/v1/devices/{device_id}/uptime`
- `GET /api/v1/devices/{device_id}/performance-trends`
- `POST /api/v1/devices/{device_id}/heartbeat`

### Parameter Health Configuration
- `POST /api/v1/devices/{device_id}/health-config`
- `GET /api/v1/devices/{device_id}/health-config`
- `GET /api/v1/devices/{device_id}/health-config/validate-weights`
- `GET /api/v1/devices/{device_id}/health-config/{config_id}`
- `PUT /api/v1/devices/{device_id}/health-config/{config_id}`
- `DELETE /api/v1/devices/{device_id}/health-config/{config_id}`
- `POST /api/v1/devices/{device_id}/health-config/bulk`
- `POST /api/v1/devices/{device_id}/health-score`

### Idle Running / Load State
- `GET /api/v1/devices/{device_id}/idle-config`
- `POST /api/v1/devices/{device_id}/idle-config`
- `GET /api/v1/devices/{device_id}/current-state`
- `GET /api/v1/devices/{device_id}/idle-stats`

## Health Score Formula (Implemented)
Code: `app/services/health_config.py`

For each configured parameter:
1. Compute raw score `0..100` from value vs normal/max bands.
2. Compute weighted score:
   - `weighted_score = raw_score * (weight / 100)`
3. Sum weighted scores across included parameters.

Overall:
- `health_score = round(sum(weighted_scores), 2)`
- If no parameters are included (missing telemetry/ignored): `health_score = null`

Raw score behavior:
- Inside normal band: `70..100` based on distance from ideal center.
- Outside normal but inside max band: `25..69`.
- Beyond max band: drops toward `0`.

## Idle/Load State Rules
Code: `app/services/idle_running.py`

State detection:
- `unloaded`: `current <= 0 && voltage > 0`
- `idle`: `0 < current < threshold && voltage > 0`
- `running`: `current >= threshold && voltage > 0`
- `unknown`: missing fields, stale conditions, or threshold unavailable where required

Power for idle energy:
- direct power if available
- else derived `power_kw = (current * voltage * pf) / 1000`
- if PF missing -> assume `pf=1.0`, mark `pf_estimated=true`

Idle cost:
- computed from live tariff settings using cached tariff fetch (TTL 60s)

## Runtime/Status Contract
- Runtime `running/stopped` comes from heartbeat freshness logic.
- Load state (`in load/idle/unloaded/unknown`) is separate electrical-state logic.
- UI precedence: if runtime is stopped, load badge should display Unknown.

## Dashboard Widget Config Contract
- Widget config is persisted per-device in DB (`device_dashboard_widgets`), not UI-local state.
- `GET /dashboard-widgets` returns:
  - `available_fields`: discovered numeric telemetry fields
  - `selected_fields`: explicit persisted selection
  - `effective_fields`: rendering set (selected, or fallback-all if no selection)
  - `default_applied`: `true` when fallback-all is active
- `PUT /dashboard-widgets` is idempotent full-replace of `selected_fields`.
- Validation: unknown/unavailable fields are rejected with HTTP `422`.
- Display-only filter: backend calculations and ingestion remain full-fidelity across all telemetry fields.

## Storage / Migrations
- Uses MySQL + Alembic migrations.
- Alembic version table in this service is namespaced (`alembic_version_device`) for single-DB deployments.
- Startup applies migrations automatically with a guarded baseline-stamp check for legacy pre-migrated schemas.
- Includes one-time exact-duplicate cleanup for `device_shifts` (keeps oldest row per exact key).
