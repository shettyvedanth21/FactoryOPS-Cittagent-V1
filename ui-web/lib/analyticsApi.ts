import { ANALYTICS_SERVICE_BASE } from "./api";

export type AnalyticsType = "anomaly" | "prediction" | "forecast";

export interface RunAnalyticsRequest {
  device_id: string;
  analysis_type: AnalyticsType;
  model_name: string;
  dataset_key?: string;
  parameters?: Record<string, any>;
  start_time?: string;
  end_time?: string;
}

export interface RunFleetAnalyticsRequest {
  device_ids?: string[];
  start_time: string;
  end_time: string;
  analysis_type: "anomaly" | "prediction";
  model_name?: string;
  parameters?: Record<string, any>;
}

export interface AnomalyFormattedResult {
  analysis_type: "anomaly_detection";
  device_id: string;
  job_id: string;
  health_score: number;
  confidence?: {
    level: string;
    badge_color: string;
    banner_text: string;
    banner_style: string;
    days_available: number;
  };
  summary: {
    total_anomalies: number;
    anomaly_rate_pct: number;
    anomaly_score: number;
    health_impact: "Normal" | "Low" | "Moderate" | "Critical";
    most_affected_parameter: string;
    data_points_analyzed: number;
    days_analyzed: number;
    model_confidence: string;
    sensitivity: string;
  };
  anomaly_rate_gauge?: {
    value: number;
    max: number;
    color: "green" | "amber" | "red";
  };
  parameter_breakdown: Array<{
    parameter: string;
    anomaly_count: number;
    anomaly_pct: number;
    severity_distribution: { low: number; medium: number; high: number };
  }>;
  anomalies_over_time: Array<{
    date: string;
    count: number;
    high_count: number;
    medium_count: number;
    low_count: number;
  }>;
  anomaly_list: Array<{
    timestamp: string;
    severity: "low" | "medium" | "high";
    parameters: string[];
    context: string;
    reasoning: string;
    recommended_action: string;
  }>;
  recommendations: Array<{
    rank: number;
    action: string;
    urgency: string;
    reasoning: string;
    parameter?: string;
  }>;
  metadata: Record<string, any>;
}

export interface FailureFormattedResult {
  analysis_type: "failure_prediction";
  device_id: string;
  job_id: string;
  health_score: number;
  confidence?: {
    level: string;
    badge_color: string;
    banner_text: string;
    banner_style: string;
    days_available: number;
  };
  summary: {
    failure_risk: "Minimal" | "Low" | "Medium" | "High" | "Critical";
    failure_probability_pct: number;
    failure_probability_meter: number;
    safe_probability_pct?: number;
    estimated_remaining_life: string;
    maintenance_urgency: string;
    confidence_level: string;
    days_analyzed: number;
  };
  risk_breakdown: { safe_pct: number; warning_pct: number; critical_pct: number };
  risk_factors: Array<{
    parameter: string;
    contribution_pct: number;
    trend: "increasing" | "decreasing" | "stable" | "erratic";
    context: string;
    reasoning: string;
    current_value: number;
    baseline_value: number;
  }>;
  insufficient_trend_signal?: boolean;
  recommended_actions: Array<{
    rank: number;
    action: string;
    urgency: string;
    reasoning: string;
    parameter?: string;
  }>;
  metadata: Record<string, any>;
}

export interface FleetFormattedResult {
  analysis_type: "fleet";
  job_id: string;
  fleet_health_score: number;
  worst_device_id: string | null;
  worst_device_health: number;
  critical_devices: string[];
  source_analysis_type: string;
  device_summaries: Array<{
    device_id: string;
    health_score: number;
    failure_risk?: string;
    total_anomalies?: number;
    anomaly_rate_pct?: number;
    maintenance_urgency?: string;
    child_job_id?: string;
  }>;
}

export interface SupportedModelsResponse {
  anomaly_detection: string[];
  failure_prediction: string[];
  forecasting: string[];
}

export interface AvailableDataset {
  key: string;
  size: number;
  last_modified: string;
}

export interface AvailableDatasetsResponse {
  device_id: string;
  datasets: AvailableDataset[];
}

export async function runAnalytics(payload: RunAnalyticsRequest) {
  const res = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<{ job_id: string; status: string; message: string }>;
}

export async function runFleetAnalytics(payload: RunFleetAnalyticsRequest) {
  const res = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/run-fleet`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<{ job_id: string; status: string; message: string }>;
}

export async function getAnalyticsStatus(jobId: string) {
  const res = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/status/${jobId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<{ job_id: string; status: string; progress: number; message: string }>;
}

export async function getAnalyticsResults(jobId: string) {
  const res = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/results/${jobId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<any>;
}

export async function getFormattedResults(
  jobId: string
): Promise<AnomalyFormattedResult | FailureFormattedResult | FleetFormattedResult> {
  const formattedRes = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/formatted-results/${jobId}`);
  if (formattedRes.ok) return formattedRes.json();

  const rawRes = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/results/${jobId}`);
  if (!rawRes.ok) throw new Error(`HTTP ${rawRes.status}`);
  const data = await rawRes.json();

  const formatted = data?.results?.formatted ?? data?.formatted ?? null;
  if (!formatted?.analysis_type) {
    throw new Error("No formatted results available for this job");
  }
  return formatted;
}

export async function getRetrainStatus(): Promise<Record<string, any>> {
  const res = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/retrain-status`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getSupportedModels(): Promise<SupportedModelsResponse> {
  const res = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/models`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getAvailableDatasets(deviceId: string): Promise<AvailableDatasetsResponse> {
  const res = await fetch(`${ANALYTICS_SERVICE_BASE}/api/v1/analytics/datasets?device_id=${deviceId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
