# Data Service

Telemetry ingestion and query service for FactoryOPS.

This service ingests MQTT telemetry, validates/enriches it, writes to InfluxDB, triggers rule evaluation, and exposes REST/WebSocket read APIs.

## Base URL
- `http://<host>:8081`
- API prefix from config: `settings.api_prefix` (default: `/api/v1/data`)

## Health Endpoints
- `GET /` (service info)
- `GET /api/v1/data/health`
- `GET /ws/stats` (websocket runtime stats)

## REST API
Under default prefix `/api/v1/data`:
- `GET /telemetry/{device_id}`
  - Query windowed telemetry with optional field selection/aggregation.
- `GET /stats/{device_id}`
  - Aggregated stats for a device.
- `POST /query`
  - Custom query payload for advanced reads.

Additional direct telemetry router endpoints are also mounted:
- `GET /telemetry/{device_id}`
- `GET /stats/{device_id}`

## Runtime Pipeline
1. MQTT subscription receives telemetry.
2. Payload validation and schema checks.
3. Device metadata enrichment.
4. InfluxDB write (device tag isolation).
5. Async rule-engine evaluation call.
6. WebSocket broadcast to subscribers.

## MQTT Contract
- Configured topic pattern default: `devices/+/telemetry`
- Message must include valid device identity and telemetry payload.
- Ingestion validates topic/device mapping before persistence.

## Query/Storage Model
- InfluxDB stores device telemetry as time-series.
- Device isolation is enforced via `device_id` tag in queries and writes.
- Time-bounded reads are used for API performance.

## Configuration
File: `src/config/settings.py`
Key env groups:
- MQTT
- InfluxDB
- Device-service URL
- Rule-engine URL
- API prefix
- validation ranges
- websocket limits

## Reliability and Safety
- Structured error responses for API failures.
- Retry/circuit-breaker patterns for downstream calls.
- Dead-letter support for invalid telemetry messages.
