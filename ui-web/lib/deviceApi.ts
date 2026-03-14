import { DEVICE_SERVICE_BASE } from "./api";

/**
 * Raw backend shape
 */
interface BackendDevice {
  device_id: string;
  device_name: string;
  device_type: string;
  data_source_type?: "metered" | "sensor";
  status: string;
  location: string | null;
  runtime_status: string;
  last_seen_timestamp: string | null;
}

/**
 * UI shape - uses runtime_status for dynamic device state
 */
export interface Device {
  id: string;
  name: string;
  type: string;
  data_source_type?: "metered" | "sensor";
  status: string;
  runtime_status: string;
  last_seen_timestamp: string | null;
  location: string;
}

export type DeviceLoadState = "running" | "idle" | "unloaded" | "unknown";

export interface IdleConfig {
  device_id: string;
  idle_current_threshold: number | null;
  configured: boolean;
}

export interface DeviceWasteConfig {
  device_id: string;
  overconsumption_current_threshold_a: number | null;
  unoccupied_weekday_start_time: string | null;
  unoccupied_weekday_end_time: string | null;
  unoccupied_weekend_start_time: string | null;
  unoccupied_weekend_end_time: string | null;
  has_device_override: boolean;
}

export interface CurrentState {
  device_id: string;
  state: DeviceLoadState;
  current: number | null;
  voltage: number | null;
  threshold: number | null;
  timestamp: string | null;
  current_field: string | null;
  voltage_field: string | null;
}

export interface IdlePeriodStats {
  idle_duration_minutes: number;
  idle_duration_label: string;
  idle_energy_kwh: number;
  idle_cost: number | null;
  currency: string;
}

export interface IdleStats {
  device_id: string;
  today: IdlePeriodStats | null;
  month: IdlePeriodStats | null;
  tariff_configured: boolean;
  pf_estimated: boolean;
  threshold_configured: boolean;
  idle_current_threshold: number | null;
  data_source_type: "metered" | "sensor" | string;
  tariff_cache?: string;
  tariff_stale?: boolean;
}

export interface DashboardWidgetConfig {
  device_id: string;
  available_fields: string[];
  selected_fields: string[];
  effective_fields: string[];
  default_applied: boolean;
}

interface DeviceApiResponse<T> {
  success: boolean;
  data: T;
}

async function readApiError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body?.message) return String(body.message);
    if (body?.error?.message) return String(body.error.message);
    if (body?.detail?.message) return String(body.detail.message);
    if (typeof body?.detail === "string") return body.detail;
    return `HTTP ${res.status}`;
  } catch {
    const text = await res.text();
    return text || `HTTP ${res.status}`;
  }
}

/* ----------------------- */
/* Mapping (single place) */
/* ----------------------- */

function mapDevice(d: BackendDevice): Device {
  return {
    id: d.device_id,
    name: d.device_name,
    type: d.device_type,
    data_source_type: d.data_source_type,
    status: d.status,
    runtime_status: d.runtime_status || "stopped",
    last_seen_timestamp: d.last_seen_timestamp,
    location: d.location ?? "",
  };
}

/* ----------------------- */

export async function getDevices(): Promise<Device[]> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  const json: DeviceApiResponse<BackendDevice[]> = await res.json();

  return (json.data || []).map(mapDevice);
}

export async function getDeviceById(deviceId: string): Promise<Device | null> {
  if (!deviceId) return null;

  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}`
  );

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  const json: DeviceApiResponse<BackendDevice> = await res.json();

  return json.data ? mapDevice(json.data) : null;
}

export async function getIdleConfig(deviceId: string): Promise<IdleConfig> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/idle-config`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return {
    device_id: json.device_id,
    idle_current_threshold: json.idle_current_threshold,
    configured: Boolean(json.configured),
  };
}

export async function saveIdleConfig(deviceId: string, idleCurrentThreshold: number): Promise<IdleConfig> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/idle-config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ idle_current_threshold: idleCurrentThreshold }),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return {
    device_id: json.device_id,
    idle_current_threshold: json.idle_current_threshold,
    configured: Boolean(json.configured),
  };
}

export async function getCurrentState(deviceId: string): Promise<CurrentState> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/current-state`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return {
    device_id: json.device_id,
    state: json.state ?? "unknown",
    current: json.current ?? null,
    voltage: json.voltage ?? null,
    threshold: json.threshold ?? null,
    timestamp: json.timestamp ?? null,
    current_field: json.current_field ?? null,
    voltage_field: json.voltage_field ?? null,
  };
}

export async function getIdleStats(deviceId: string): Promise<IdleStats> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/idle-stats`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return {
    device_id: json.device_id,
    today: json.today ?? null,
    month: json.month ?? null,
    tariff_configured: Boolean(json.tariff_configured),
    pf_estimated: Boolean(json.pf_estimated),
    threshold_configured: Boolean(json.threshold_configured),
    idle_current_threshold: json.idle_current_threshold ?? null,
    data_source_type: json.data_source_type,
    tariff_cache: json.tariff_cache,
    tariff_stale: json.tariff_stale,
  };
}

export async function getDeviceWasteConfig(deviceId: string): Promise<DeviceWasteConfig> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/waste-config`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await readApiError(res));
  }
  const json = await res.json();
  return {
    device_id: json.device_id,
    overconsumption_current_threshold_a: json.overconsumption_current_threshold_a ?? null,
    unoccupied_weekday_start_time: json.unoccupied_weekday_start_time ?? null,
    unoccupied_weekday_end_time: json.unoccupied_weekday_end_time ?? null,
    unoccupied_weekend_start_time: json.unoccupied_weekend_start_time ?? null,
    unoccupied_weekend_end_time: json.unoccupied_weekend_end_time ?? null,
    has_device_override: Boolean(json.has_device_override),
  };
}

export async function saveDeviceWasteConfig(
  deviceId: string,
  payload: {
    overconsumption_current_threshold_a: number | null;
    unoccupied_weekday_start_time: string | null;
    unoccupied_weekday_end_time: string | null;
    unoccupied_weekend_start_time: string | null;
    unoccupied_weekend_end_time: string | null;
  }
): Promise<DeviceWasteConfig> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/waste-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await readApiError(res));
  }
  const json = await res.json();
  return {
    device_id: json.device_id,
    overconsumption_current_threshold_a: json.overconsumption_current_threshold_a ?? null,
    unoccupied_weekday_start_time: json.unoccupied_weekday_start_time ?? null,
    unoccupied_weekday_end_time: json.unoccupied_weekday_end_time ?? null,
    unoccupied_weekend_start_time: json.unoccupied_weekend_start_time ?? null,
    unoccupied_weekend_end_time: json.unoccupied_weekend_end_time ?? null,
    has_device_override: Boolean(json.has_device_override),
  };
}

export async function getDashboardWidgetConfig(deviceId: string): Promise<DashboardWidgetConfig> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/dashboard-widgets`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return {
    device_id: json.device_id,
    available_fields: json.available_fields ?? [],
    selected_fields: json.selected_fields ?? [],
    effective_fields: json.effective_fields ?? [],
    default_applied: Boolean(json.default_applied),
  };
}

export async function saveDashboardWidgetConfig(
  deviceId: string,
  selectedFields: string[]
): Promise<DashboardWidgetConfig> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/dashboard-widgets`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_fields: selectedFields }),
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `HTTP ${res.status}`);
  }
  const json = await res.json();
  return {
    device_id: json.device_id,
    available_fields: json.available_fields ?? [],
    selected_fields: json.selected_fields ?? [],
    effective_fields: json.effective_fields ?? [],
    default_applied: Boolean(json.default_applied),
  };
}


/* =====================================================
 * Shift Configuration API
 * ===================================================== */

export interface Shift {
  id: number;
  device_id: string;
  shift_name: string;
  shift_start: string;  // HH:MM format
  shift_end: string;    // HH:MM format
  maintenance_break_minutes: number;
  day_of_week: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ShiftCreate {
  shift_name: string;
  shift_start: string;
  shift_end: string;
  maintenance_break_minutes: number;
  day_of_week?: number | null;
  is_active?: boolean;
}

export interface UptimeData {
  device_id: string;
  uptime_percentage: number | null;
  total_planned_minutes: number;
  total_effective_minutes: number;
  actual_running_minutes?: number;
  shifts_configured: number;
  window_start?: string | null;
  window_end?: string | null;
  window_timezone?: string;
  data_coverage_pct?: number;
  data_quality?: "high" | "medium" | "low" | string;
  calculation_mode?: string;
  message: string;
}

function mapShift(s: any): Shift {
  return {
    id: s.id,
    device_id: s.device_id,
    shift_name: s.shift_name,
    shift_start: s.shift_start,
    shift_end: s.shift_end,
    maintenance_break_minutes: s.maintenance_break_minutes,
    day_of_week: s.day_of_week,
    is_active: s.is_active,
    created_at: s.created_at,
    updated_at: s.updated_at,
  };
}

export async function getShifts(deviceId: string): Promise<Shift[]> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/shifts`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return (json.data || []).map(mapShift);
}

export async function createShift(deviceId: string, shift: ShiftCreate): Promise<Shift> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/shifts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(shift),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res));
  }
  const json = await res.json();
  return mapShift(json.data);
}

export async function updateShift(
  deviceId: string,
  shiftId: number,
  shift: Partial<ShiftCreate>
): Promise<Shift> {
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/shifts/${shiftId}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(shift),
    }
  );
  if (!res.ok) {
    throw new Error(await readApiError(res));
  }
  const json = await res.json();
  return mapShift(json.data);
}

export async function deleteShift(deviceId: string, shiftId: number): Promise<void> {
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/shifts/${shiftId}`,
    { method: "DELETE" }
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
}

export async function getUptime(deviceId: string): Promise<UptimeData> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/uptime`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}


/* =====================================================
 * Health Configuration API
 * ===================================================== */

export interface HealthConfig {
  id: number;
  device_id: string;
  parameter_name: string;
  normal_min: number | null;
  normal_max: number | null;
  max_min: number | null;
  max_max: number | null;
  weight: number;
  ignore_zero_value: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface HealthConfigCreate {
  parameter_name: string;
  normal_min?: number | null;
  normal_max?: number | null;
  max_min?: number | null;
  max_max?: number | null;
  weight: number;
  ignore_zero_value?: boolean;
  is_active?: boolean;
}

export interface WeightValidation {
  is_valid: boolean;
  total_weight: number;
  message: string;
  parameters: Array<{
    parameter_name: string;
    weight: number;
    is_active: boolean;
  }>;
}

export interface ParameterScore {
  parameter_name: string;
  value: number;
  raw_score: number;
  weighted_score: number;
  weight: number;
  status: string;
  status_color: string;
}

export interface HealthScore {
  device_id: string;
  health_score: number | null;
  status: string;
  status_color: string;
  message: string;
  machine_state: string;
  parameter_scores: ParameterScore[];
  total_weight_configured: number;
  parameters_included: number;
  parameters_skipped: number;
}

export type PerformanceTrendMetric = "health" | "uptime";
export type PerformanceTrendRange = "30m" | "1h" | "6h" | "24h" | "7d" | "30d";

export interface PerformanceTrendPoint {
  timestamp: string;
  health_score: number | null;
  uptime_percentage: number | null;
  planned_minutes: number;
  effective_minutes: number;
  break_minutes: number;
}

export interface PerformanceTrendData {
  device_id: string;
  metric: PerformanceTrendMetric;
  range: PerformanceTrendRange;
  interval_minutes: number;
  timezone: string;
  points: PerformanceTrendPoint[];
  total_points: number;
  sampled_points: number;
  message: string;
}

export interface DashboardDeviceItem {
  device_id: string;
  device_name: string;
  device_type: string;
  runtime_status: string;
  location: string | null;
  last_seen_timestamp: string | null;
  health_score: number | null;
  uptime_percentage: number | null;
}

export interface DashboardSystemSummary {
  total_devices: number;
  running_devices: number;
  stopped_devices: number;
  devices_with_health_data: number;
  devices_with_uptime_configured: number;
  devices_missing_uptime_config: number;
  system_health: number | null;
  average_efficiency: number | null;
}

export interface DashboardAlertsSummary {
  active_alerts: number;
  alerts_triggered: number;
  alerts_cleared: number;
  rules_created: number;
}

export interface DashboardSummaryData {
  generated_at: string;
  summary: DashboardSystemSummary;
  alerts: DashboardAlertsSummary;
  devices: DashboardDeviceItem[];
}

export interface TelemetryValues {
  values: Record<string, number>;
  machine_state?: string;
}

function mapHealthConfig(c: any): HealthConfig {
  return {
    id: c.id,
    device_id: c.device_id,
    parameter_name: c.parameter_name,
    normal_min: c.normal_min,
    normal_max: c.normal_max,
    max_min: c.max_min,
    max_max: c.max_max,
    weight: c.weight,
    ignore_zero_value: c.ignore_zero_value,
    is_active: c.is_active,
    created_at: c.created_at,
    updated_at: c.updated_at,
  };
}

export async function getHealthConfigs(deviceId: string): Promise<HealthConfig[]> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/health-config`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return (json.data || []).map(mapHealthConfig);
}

export async function createHealthConfig(
  deviceId: string,
  config: HealthConfigCreate
): Promise<HealthConfig> {
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/health-config`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    }
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return mapHealthConfig(json.data);
}

export async function updateHealthConfig(
  deviceId: string,
  configId: number,
  config: Partial<HealthConfigCreate>
): Promise<HealthConfig> {
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/health-config/${configId}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    }
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return mapHealthConfig(json.data);
}

export async function deleteHealthConfig(
  deviceId: string,
  configId: number
): Promise<void> {
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/health-config/${configId}`,
    { method: "DELETE" }
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
}

export async function validateHealthWeights(
  deviceId: string
): Promise<WeightValidation> {
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/health-config/validate-weights`
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

export async function bulkCreateHealthConfigs(
  deviceId: string,
  configs: HealthConfigCreate[]
): Promise<HealthConfig[]> {
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/health-config/bulk`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(configs),
    }
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return (json.data || []).map(mapHealthConfig);
}

export async function calculateHealthScore(
  deviceId: string,
  telemetry: TelemetryValues
): Promise<HealthScore> {
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/health-score`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(telemetry),
    }
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

export async function getPerformanceTrends(
  deviceId: string,
  metric: PerformanceTrendMetric,
  range: PerformanceTrendRange
): Promise<PerformanceTrendData> {
  const query = new URLSearchParams({
    metric,
    range,
  });
  const res = await fetch(
    `${DEVICE_SERVICE_BASE}/api/v1/devices/${deviceId}/performance-trends?${query.toString()}`
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

export async function getDashboardSummary(): Promise<DashboardSummaryData> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/devices/dashboard/summary`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const json = await res.json();
  return {
    generated_at: json.generated_at,
    summary: json.summary,
    alerts: json.alerts,
    devices: json.devices || [],
  };
}
