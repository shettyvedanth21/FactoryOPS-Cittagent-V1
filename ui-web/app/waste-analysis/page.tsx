"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getDevices, Device } from "@/lib/deviceApi";
import { getTariffConfig } from "@/lib/settingsApi";
import { formatIST } from "@/lib/utils";
import {
  getWasteDownload,
  getWasteHistory,
  getWasteResult,
  getWasteStatus,
  runWasteAnalysis,
  WasteGranularity,
  WasteScope,
  WasteStatus,
} from "@/lib/wasteApi";

interface WasteHistoryRow {
  job_id: string;
  job_name?: string;
  status: string;
  error_code?: string;
  error_message?: string;
  created_at?: string;
  completed_at?: string;
  progress_pct: number;
}

interface WastageCategoryView {
  duration_sec?: number | null;
  energy_kwh?: number | null;
  cost?: number | null;
  skipped_reason?: string | null;
  pf_estimated?: boolean;
  config_source?: string | null;
  config_used?: Record<string, unknown> | null;
}

interface WasteResultDevice {
  device_id: string;
  device_name?: string;
  off_hours?: WastageCategoryView;
  overconsumption?: WastageCategoryView;
  offhours_duration_sec?: number | null;
  offhours_energy_kwh?: number | null;
  offhours_cost?: number | null;
  offhours_skipped_reason?: string | null;
  offhours_pf_estimated?: boolean;
  overconsumption_duration_sec?: number | null;
  overconsumption_kwh?: number | null;
  overconsumption_cost?: number | null;
  overconsumption_skipped_reason?: string | null;
  overconsumption_pf_estimated?: boolean;
}

interface WasteResultPayload {
  total_waste_cost?: number | null;
  device_summaries?: WasteResultDevice[];
}

function formatDurationSeconds(seconds?: number | null): string {
  if (seconds == null) return "—";
  const mins = Math.round(seconds / 60);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h} hr ${m} min` : `${m} min`;
}

function WastageRow({
  label,
  duration,
  kwh,
  cost,
  skippedReason,
  pfEstimated,
  configSource,
}: {
  label: string;
  duration?: number | null;
  kwh?: number | null;
  cost?: number | null;
  skippedReason?: string | null;
  pfEstimated?: boolean;
  configSource?: string | null;
}) {
  if (skippedReason) {
    return (
      <tr className="text-gray-400">
        <td className="py-2 pr-4">{label}</td>
        <td colSpan={3} className="py-2 pr-4 text-sm italic">{skippedReason}</td>
      </tr>
    );
  }

  return (
    <tr>
      <td className="py-2 pr-4">
        {label}
        {pfEstimated && <span className="ml-1 text-xs text-amber-700" title="Power factor estimated at 0.85">*</span>}
        {configSource && (
          <span className="ml-2 text-[10px] uppercase tracking-wide text-slate-500">({configSource})</span>
        )}
      </td>
      <td className="py-2 pr-4">{formatDurationSeconds(duration)}</td>
      <td className="py-2 pr-4">{kwh != null ? `${Number(kwh).toFixed(2)} kWh` : "—"}</td>
      <td className="py-2 pr-4">{cost != null ? `₹${Number(cost).toFixed(2)}` : "—"}</td>
    </tr>
  );
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultStartDate(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return isoDate(d);
}

export default function WasteAnalysisPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [scope, setScope] = useState<WasteScope>("all");
  const [selected, setSelected] = useState<string[]>([]);
  const [startDate, setStartDate] = useState(defaultStartDate);
  const [endDate, setEndDate] = useState(isoDate(new Date()));
  const [granularity, setGranularity] = useState<WasteGranularity>("daily");
  const [jobName, setJobName] = useState("");

  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<WasteStatus | null>(null);
  const [history, setHistory] = useState<WasteHistoryRow[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tariffBanner, setTariffBanner] = useState<string | null>(null);
  const [thresholdBlockingMsg] = useState<string | null>(
    "Missing threshold no longer blocks report generation. Overconsumption will be marked as skipped with reason."
  );
  const [resultData, setResultData] = useState<WasteResultPayload | null>(null);
  const autoDownloadedJobIds = useRef<Set<string>>(new Set());

  const selectedValid = useMemo(() => selected.filter(Boolean), [selected]);
  async function loadHistory() {
    try {
      const h = await getWasteHistory(20, 0);
      setHistory(h.items || []);
    } catch {
      setError("Failed to load waste analysis history");
    }
  }

  useEffect(() => {
    async function bootstrap() {
      try {
        const [ds, tariff] = await Promise.all([getDevices(), getTariffConfig()]);
        setDevices(ds);
        if (!tariff?.rate) {
          setTariffBanner("Tariff not configured. Cost calculations will be unavailable. Configure in Settings -> Tariff Configuration.");
        }
      } catch {
        setError("Failed to initialize waste analysis page");
      }
      await loadHistory();
    }
    bootstrap();
  }, []);

  const onDownload = useCallback(async (id: string) => {
    try {
      const d = await getWasteDownload(id);
      const link = document.createElement("a");
      link.href = d.download_url;
      link.target = "_self";
      link.rel = "noopener noreferrer";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch {
      setError("Failed to fetch download URL");
    }
  }, []);

  useEffect(() => {
    if (!jobId || !running) return;
    const timer = setInterval(async () => {
      try {
        const s = await getWasteStatus(jobId);
        setStatus(s);
        if (s.status === "completed" || s.status === "failed") {
          setRunning(false);
          if (s.status === "completed" && !autoDownloadedJobIds.current.has(jobId)) {
            autoDownloadedJobIds.current.add(jobId);
            await onDownload(jobId);
          }
          await loadHistory();
        }
      } catch {
        setRunning(false);
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [jobId, running, onDownload]);

  async function onRun() {
    setError(null);
    if (scope === "selected" && selectedValid.length === 0) {
      setError("Select at least one device for selected scope.");
      return;
    }

    try {
      setRunning(true);
      setStatus(null);
      const res = await runWasteAnalysis({
        job_name: jobName || undefined,
        scope,
        device_ids: scope === "all" ? null : selectedValid,
        start_date: startDate,
        end_date: endDate,
        granularity,
      });
      setJobId(res.job_id);
      autoDownloadedJobIds.current.delete(res.job_id);
      setStatus({
        job_id: res.job_id,
        status: "pending",
        progress_pct: 0,
        stage: "Queued",
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to run analysis";
      setRunning(false);
      setError(msg);
    }
  }

  async function onViewResult(id: string) {
    try {
      const result = await getWasteResult(id);
      setResultData((result || null) as WasteResultPayload | null);
    } catch {
      setError("Failed to load result");
    }
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Waste Energy Analysis</h1>
        <p className="text-gray-600">Configure and generate waste analysis reports</p>
      </div>

      {tariffBanner && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 text-amber-800 px-4 py-3 text-sm">
          {tariffBanner}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 text-red-700 px-4 py-3 text-sm">{error}</div>
      )}
      {thresholdBlockingMsg && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 text-amber-800 px-4 py-3 text-sm">
          {thresholdBlockingMsg}
        </div>
      )}

      <div className="bg-white rounded-xl border p-5 space-y-4">
        <h2 className="text-lg font-semibold text-gray-900">Configure Analysis</h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm text-gray-700 mb-1">Scope</label>
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as WasteScope)}
              className="w-full border rounded-lg px-3 py-2"
            >
              <option value="all">All Devices</option>
              <option value="selected">Selected Devices</option>
            </select>
          </div>

          <div>
            <label className="block text-sm text-gray-700 mb-1">Granularity</label>
            <select
              value={granularity}
              onChange={(e) => setGranularity(e.target.value as WasteGranularity)}
              className="w-full border rounded-lg px-3 py-2"
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm text-gray-700 mb-1">Start Date</label>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="w-full border rounded-lg px-3 py-2" />
          </div>
          <div>
            <label className="block text-sm text-gray-700 mb-1">End Date</label>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="w-full border rounded-lg px-3 py-2" />
          </div>
        </div>

        {scope === "selected" && (
          <div>
            <label className="block text-sm text-gray-700 mb-1">Select Devices</label>
            <select
              multiple
              value={selected}
              onChange={(e) => setSelected(Array.from(e.target.selectedOptions).map((opt) => opt.value))}
              className="w-full border rounded-lg px-3 py-2 h-32"
            >
              {devices.map((d) => (
                <option key={d.id} value={d.id}>{d.name} ({d.id})</option>
              ))}
            </select>
          </div>
        )}

        <div>
          <label className="block text-sm text-gray-700 mb-1">Job Name (optional)</label>
          <input
            value={jobName}
            onChange={(e) => setJobName(e.target.value)}
            className="w-full border rounded-lg px-3 py-2"
            placeholder="Weekly Waste Report"
          />
        </div>

        <button
          onClick={onRun}
          disabled={running}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-300"
        >
          {running ? "Generating report..." : "Run Wastage Analysis"}
        </button>

        {status && (
          <div className="mt-2 border rounded-lg p-3 bg-gray-50">
            <div className="text-sm font-medium text-gray-800">{status.stage || "Processing..."}</div>
            <div className="mt-2 h-2 rounded bg-gray-200 overflow-hidden">
              <div className="h-full bg-blue-600 transition-all" style={{ width: `${status.progress_pct}%` }} />
            </div>
            <div className="mt-1 text-xs text-gray-600">{status.progress_pct}% • {status.status}</div>
            {(status.error_code || status.error_message) && (
              <div className="mt-1 text-xs text-red-700">
                {status.error_code ? `${status.error_code}: ` : ""}
                {status.error_message || "Report failed"}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border p-5">
        <h2 className="text-lg font-semibold text-gray-900 mb-3">Analysis History</h2>
        {history.length === 0 ? (
          <div className="text-sm text-gray-500">No analysis runs yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="py-2 pr-4">Created</th>
                  <th className="py-2 pr-4">Job</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Reason</th>
                  <th className="py-2 pr-4">Actions</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.job_id} className="border-b">
                    <td className="py-2 pr-4">{formatIST(h.created_at ?? null, "-")}</td>
                    <td className="py-2 pr-4">{h.job_name || h.job_id.slice(0, 8)}</td>
                    <td className="py-2 pr-4 capitalize">{h.status}</td>
                    <td className="py-2 pr-4 text-xs text-gray-600">
                      {h.error_code ? `${h.error_code}${h.error_message ? `: ${h.error_message}` : ""}` : "-"}
                    </td>
                    <td className="py-2 pr-4 space-x-3">
                      {h.status === "completed" && (
                        <>
                          <button className="text-blue-600 hover:text-blue-800" onClick={() => onDownload(h.job_id)}>Download PDF</button>
                          <button className="text-gray-600 hover:text-gray-800" onClick={() => onViewResult(h.job_id)}>View JSON</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {resultData && (
        <div className="bg-white rounded-xl border p-5 space-y-4">
          <h2 className="text-lg font-semibold text-gray-900">Result Breakdown</h2>
          <div className="text-sm text-gray-600">
            Total Waste Cost: {resultData.total_waste_cost != null ? `₹${Number(resultData.total_waste_cost).toFixed(2)}` : "N/A"}
          </div>
          <div className="space-y-4">
            {(resultData.device_summaries || []).map((device: WasteResultDevice) => (
              <div key={device.device_id} className="border rounded-lg p-3">
                <div className="font-medium text-gray-900 mb-2">{device.device_name || device.device_id}</div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="text-left text-gray-500 border-b">
                        <th className="py-2 pr-4">Category</th>
                        <th className="py-2 pr-4">Duration</th>
                        <th className="py-2 pr-4">Energy</th>
                        <th className="py-2 pr-4">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      <WastageRow
                        label="Off-Hours Running"
                        duration={device?.off_hours?.duration_sec ?? device?.offhours_duration_sec}
                        kwh={device?.off_hours?.energy_kwh ?? device?.offhours_energy_kwh}
                        cost={device?.off_hours?.cost ?? device?.offhours_cost}
                        skippedReason={device?.off_hours?.skipped_reason ?? device?.offhours_skipped_reason}
                        pfEstimated={Boolean(device?.off_hours?.pf_estimated ?? device?.offhours_pf_estimated)}
                        configSource={device?.off_hours?.config_source}
                      />
                      <WastageRow
                        label="Overconsumption"
                        duration={device?.overconsumption?.duration_sec ?? device?.overconsumption_duration_sec}
                        kwh={device?.overconsumption?.energy_kwh ?? device?.overconsumption_kwh}
                        cost={device?.overconsumption?.cost ?? device?.overconsumption_cost}
                        skippedReason={device?.overconsumption?.skipped_reason ?? device?.overconsumption_skipped_reason}
                        pfEstimated={Boolean(device?.overconsumption?.pf_estimated ?? device?.overconsumption_pf_estimated)}
                        configSource={device?.overconsumption?.config_source}
                      />
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
