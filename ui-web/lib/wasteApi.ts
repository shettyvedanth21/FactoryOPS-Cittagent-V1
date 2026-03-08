import { WASTE_SERVICE_BASE } from "./api";

export type WasteScope = "all" | "selected";
export type WasteGranularity = "daily" | "weekly" | "monthly";

export interface WasteRunParams {
  job_name?: string;
  scope: WasteScope;
  device_ids?: string[] | null;
  start_date: string;
  end_date: string;
  granularity: WasteGranularity;
}

export interface WasteRunResponse {
  job_id: string;
  status: string;
  estimated_completion_seconds: number;
}

export interface WasteStatus {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed";
  progress_pct: number;
  stage?: string;
  error_code?: string;
  error_message?: string;
}

export interface WasteHistoryItem {
  job_id: string;
  job_name?: string;
  status: string;
  error_code?: string;
  error_message?: string;
  created_at?: string;
  completed_at?: string;
  progress_pct: number;
}

export async function runWasteAnalysis(params: WasteRunParams): Promise<WasteRunResponse> {
  const res = await fetch(`${WASTE_SERVICE_BASE}/analysis/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.detail?.message || err?.detail || err?.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getWasteStatus(jobId: string): Promise<WasteStatus> {
  const res = await fetch(`${WASTE_SERVICE_BASE}/analysis/${jobId}/status`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getWasteResult(jobId: string): Promise<unknown> {
  const res = await fetch(`${WASTE_SERVICE_BASE}/analysis/${jobId}/result`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getWasteDownload(jobId: string): Promise<{ download_url: string }> {
  const res = await fetch(`${WASTE_SERVICE_BASE}/analysis/${jobId}/download`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getWasteHistory(limit = 20, offset = 0): Promise<{ items: WasteHistoryItem[] }> {
  const res = await fetch(`${WASTE_SERVICE_BASE}/analysis/history?limit=${limit}&offset=${offset}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
