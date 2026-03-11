"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import {
  getDeviceById,
  Device,
  getIdleConfig,
  saveIdleConfig,
  getCurrentState,
  getIdleStats,
  CurrentState,
  IdleStats,
  getShifts,
  createShift,
  deleteShift,
  Shift,
  ShiftCreate,
  getUptime,
  UptimeData,
  getHealthConfigs,
  createHealthConfig,
  deleteHealthConfig,
  updateHealthConfig,
  HealthConfig,
  HealthConfigCreate,
  calculateHealthScore,
  HealthScore,
  TelemetryValues,
  validateHealthWeights,
  WeightValidation,
  getPerformanceTrends,
  PerformanceTrendData,
  PerformanceTrendRange,
  PerformanceTrendMetric,
  DashboardWidgetConfig,
  getDashboardWidgetConfig,
  saveDashboardWidgetConfig,
} from "@/lib/deviceApi";
import {
  getTelemetry,
  TelemetryPoint,
  getActivityEvents,
  getActivityUnreadCount,
  markAllActivityRead,
  clearActivityHistory,
  ActivityEvent,
} from "@/lib/dataApi";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TimeSeriesChart } from "@/components/charts/telemetry-charts";
import { MachineRulesView } from "@/app/machines/[deviceId]/rules/machine-rules-view";
import { formatIST, getRelativeTime } from "@/lib/utils";

const METRIC_LABELS: Record<string, string> = {
  power: "Power", voltage: "Voltage", current: "Current", temperature: "Temperature",
  pressure: "Pressure", humidity: "Humidity", vibration: "Vibration", frequency: "Frequency",
  power_factor: "Power Factor", speed: "Speed", torque: "Torque", oil_pressure: "Oil Pressure",
};

const METRIC_UNITS: Record<string, string> = {
  power: " W", voltage: " V", current: " A", temperature: " °C",
  pressure: " bar", humidity: " %", vibration: " mm/s", frequency: " Hz",
  power_factor: "", speed: " RPM", torque: " Nm", oil_pressure: " bar",
};

const METRIC_COLORS: Record<string, string> = {
  power: "#2563eb", voltage: "#d97706", current: "#7c3aed", temperature: "#dc2626",
  pressure: "#059669", humidity: "#0891b2", vibration: "#ea580c", frequency: "#4f46e5",
  power_factor: "#8b5cf6", speed: "#0d9488", torque: "#be185d", oil_pressure: "#65a30d",
};

const METRIC_RANGES: Record<string, [number, number]> = {
  power: [0, 500], voltage: [200, 250], current: [0, 20], temperature: [0, 120],
  pressure: [0, 10], humidity: [0, 100], vibration: [0, 10], frequency: [45, 55],
  power_factor: [0.8, 1.0], speed: [1000, 2000], torque: [0, 500], oil_pressure: [0, 5],
};

const DAYS_OF_WEEK = [
  { value: null, label: "All Days" },
  { value: 0, label: "Monday" }, { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" }, { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" }, { value: 5, label: "Saturday" }, { value: 6, label: "Sunday" },
];

const TREND_RANGE_OPTIONS: { label: string; value: PerformanceTrendRange }[] = [
  { label: "30m", value: "30m" },
  { label: "1h", value: "1h" },
  { label: "6h", value: "6h" },
  { label: "24h", value: "24h" },
  { label: "7d", value: "7d" },
  { label: "30d", value: "30d" },
];

type ShiftSegment = {
  day: number;
  start: number;
  end: number;
};

function toMinutes(timeValue: string): number | null {
  const parts = timeValue.split(":");
  if (parts.length < 2) return null;
  const hour = Number(parts[0]);
  const minute = Number(parts[1]);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return hour * 60 + minute;
}

function toDisplayTime(timeValue: string): string {
  const parts = timeValue.split(":");
  if (parts.length < 2) return timeValue;
  const hh = parts[0].padStart(2, "0");
  const mm = parts[1].padStart(2, "0");
  return `${hh}:${mm}`;
}

function isOvernightRange(startTime: string, endTime: string): boolean {
  const start = toMinutes(startTime);
  const end = toMinutes(endTime);
  if (start === null || end === null) return false;
  return end <= start;
}

function formatShiftRange(startTime: string, endTime: string): string {
  const overnight = isOvernightRange(startTime, endTime);
  return `${toDisplayTime(startTime)} - ${toDisplayTime(endTime)}${overnight ? " (+1 day)" : ""}`;
}

function buildShiftSegments(startTime: string, endTime: string, dayOfWeek: number | null): ShiftSegment[] {
  const start = toMinutes(startTime);
  const end = toMinutes(endTime);
  if (start === null || end === null || start === end) return [];

  const days = dayOfWeek === null ? [0, 1, 2, 3, 4, 5, 6] : [dayOfWeek];
  const segments: ShiftSegment[] = [];
  for (const day of days) {
    if (end > start) {
      segments.push({ day, start, end });
      continue;
    }
    segments.push({ day, start, end: 24 * 60 });
    segments.push({ day: (day + 1) % 7, start: 0, end });
  }
  return segments;
}

function hasSegmentOverlap(a: ShiftSegment, b: ShiftSegment): boolean {
  if (a.day !== b.day) return false;
  return a.start < b.end && b.start < a.end;
}

function findOverlapConflicts(candidate: ShiftCreate, existingShifts: Shift[]): Shift[] {
  const candidateSegments = buildShiftSegments(candidate.shift_start, candidate.shift_end, candidate.day_of_week ?? null);
  if (candidateSegments.length === 0) return [];

  return existingShifts.filter((shift) => {
    const shiftSegments = buildShiftSegments(shift.shift_start, shift.shift_end, shift.day_of_week);
    return candidateSegments.some((cand) => shiftSegments.some((seg) => hasSegmentOverlap(cand, seg)));
  });
}

function getDynamicMetrics(telemetry: TelemetryPoint[]): string[] {
  const latest = telemetry.at(-1);
  if (!latest) return [];
  const metrics = new Set<string>();
  for (const [key, value] of Object.entries(latest)) {
    if (key !== 'timestamp' && key !== 'device_id' && key !== 'schema_version' && 
        key !== 'enrichment_status' && key !== 'table' && typeof value === 'number') {
      metrics.add(key);
    }
  }
  return Array.from(metrics);
}

function getMetricData(telemetry: TelemetryPoint[], metric: string) {
  return telemetry.filter((t) => typeof (t as any)[metric] === "number")
    .map((t) => ({ timestamp: t.timestamp, value: (t as any)[metric] as number }));
}

function sortTelemetryAsc(items: TelemetryPoint[]): TelemetryPoint[] {
  return [...items].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );
}

function sortTelemetryDesc(items: TelemetryPoint[]): TelemetryPoint[] {
  return [...items].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );
}

function formatTimestamp(ts: string): string {
  return formatIST(ts, ts);
}

function formatMinutes(totalMinutes: number | null | undefined): string {
  if (typeof totalMinutes !== "number" || Number.isNaN(totalMinutes) || totalMinutes < 0) {
    return "—";
  }
  const rounded = Math.round(totalMinutes);
  const hrs = Math.floor(rounded / 60);
  const mins = rounded % 60;
  return `${hrs}h ${mins}m`;
}

function formatEventType(eventType: string): string {
  return eventType
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function UptimeCircle({ uptime, onClick }: { uptime: UptimeData | null; onClick: () => void }) {
  const percentage = uptime?.uptime_percentage ?? 0;
  const color = percentage >= 95 ? "#22c55e" : percentage >= 80 ? "#eab308" : "#ef4444";
  
  return (
    <div className="relative cursor-pointer group" onClick={onClick}>
      <div className="w-16 h-16">
        <svg className="w-full h-full transform -rotate-90">
          <circle cx="32" cy="32" r="28" stroke="#e2e8f0" strokeWidth="6" fill="none" />
          <circle cx="32" cy="32" r="28" stroke={color} strokeWidth="6" fill="none"
            strokeDasharray={`${(percentage / 100) * 176} 176`} className="transition-all duration-500" />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-xs font-bold">{percentage.toFixed(0)}%</span>
        </div>
      </div>
      
      <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 w-48 bg-white shadow-lg rounded-lg border p-3 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50">
        <p className="text-xs font-semibold text-slate-700 mb-2">Uptime Details</p>
        {uptime ? (
          <>
            <p className="text-xs text-slate-600">Active Shifts: <span className="font-medium">{uptime.shifts_configured}</span></p>
            {uptime.uptime_percentage === null ? (
              <p className="text-xs text-amber-700 mt-2">{uptime.message || "No active shift window right now."}</p>
            ) : (
              <>
                <p className="text-xs text-slate-600">Planned: <span className="font-medium">{formatMinutes(uptime.total_planned_minutes)}</span></p>
                <p className="text-xs text-slate-600">Effective: <span className="font-medium">{formatMinutes(uptime.total_effective_minutes)}</span></p>
                <p className="text-xs text-slate-600">Running: <span className="font-medium">{formatMinutes(uptime.actual_running_minutes)}</span></p>
                <p className="text-xs text-slate-500 mt-2">Uptime = running minutes / effective shift minutes.</p>
              </>
            )}
          </>
        ) : (
          <p className="text-xs text-slate-500">No shifts configured</p>
        )}
      </div>
    </div>
  );
}

function HealthScoreCircle({ healthScore, onClick }: { healthScore: HealthScore | null; onClick: () => void }) {
  const score = healthScore?.health_score ?? 0;
  const statusColor = healthScore?.status_color || "⚪";
  
  const colorMap: Record<string, string> = {
    "🟢": "#22c55e", "🟡": "#eab308", "🟠": "#f97316", "🔴": "#ef4444", "⚪": "#94a3b8"
  };
  const color = healthScore ? colorMap[statusColor] || "#94a3b8" : "#94a3b8";
  const isStandby = healthScore?.status === "Standby";
  
  return (
    <div className="relative cursor-pointer group" onClick={onClick}>
      <div className="w-16 h-16">
        <svg className="w-full h-full transform -rotate-90">
          <circle cx="32" cy="32" r="28" stroke="#e2e8f0" strokeWidth="6" fill="none" />
          <circle cx="32" cy="32" r="28" stroke={color} strokeWidth="6" fill="none"
            strokeDasharray={`${(score / 100) * 176} 176`} className="transition-all duration-500" />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xs font-bold">{isStandby ? "—" : `${score.toFixed(0)}%`}</span>
          <span className="text-[10px]">{isStandby ? "Standby" : statusColor}</span>
        </div>
      </div>
      
      <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 w-56 bg-white shadow-lg rounded-lg border p-3 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50">
        <p className="text-xs font-semibold text-slate-700 mb-2">Health Score Details</p>
        {healthScore ? (
          <>
            <p className="text-xs text-slate-600">Status: <span className="font-medium">{healthScore.status} {healthScore.status_color}</span></p>
            <p className="text-xs text-slate-600">Machine State: <span className="font-medium">{healthScore.machine_state}</span></p>
            <p className="text-xs text-slate-600">Parameters: <span className="font-medium">{healthScore.parameters_included} included, {healthScore.parameters_skipped} skipped</span></p>
            <p className="text-xs text-slate-600">Total Weight: <span className="font-medium">{healthScore.total_weight_configured}%</span></p>
            {healthScore.parameter_scores.length > 0 && (
              <div className="mt-2 border-t pt-2">
                <p className="text-xs font-medium text-slate-700">Parameter Scores:</p>
                {healthScore.parameter_scores.slice(0, 5).map((p) => (
                  <p key={p.parameter_name} className="text-xs text-slate-600">
                    {p.parameter_name}: {p.raw_score}% {p.status_color}
                  </p>
                ))}
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-slate-500">No health data</p>
        )}
      </div>
    </div>
  );
}

function LoadStateBadge({ state }: { state: "running" | "idle" | "unloaded" | "unknown" }) {
  const cfg: Record<string, { label: string; className: string }> = {
    running: { label: "In Load", className: "bg-emerald-100 text-emerald-800 border-emerald-200" },
    idle: { label: "Idle", className: "bg-amber-100 text-amber-800 border-amber-200" },
    unloaded: { label: "Unloaded", className: "bg-orange-100 text-orange-800 border-orange-200" },
    unknown: { label: "Unknown", className: "bg-slate-100 text-slate-700 border-slate-200" },
  };
  const item = cfg[state] || cfg.unknown;
  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${item.className}`}>
      {item.label}
    </span>
  );
}

function getEffectiveLoadState(
  runtimeStatus: string | undefined,
  rawLoadState: "running" | "idle" | "unloaded" | "unknown" | undefined,
): "running" | "idle" | "unloaded" | "unknown" {
  if ((runtimeStatus || "").toLowerCase() !== "running") {
    return "unknown";
  }
  return rawLoadState || "unknown";
}

function getEfficiencyStatus(rawScore: number): { label: string; color: string; bgColor: string } {
  if (rawScore >= 85) return { label: "Healthy", color: "text-green-700", bgColor: "bg-green-100" };
  if (rawScore >= 70) return { label: "Slight Warning", color: "text-yellow-700", bgColor: "bg-yellow-100" };
  if (rawScore >= 40) return { label: "Warning", color: "text-orange-700", bgColor: "bg-orange-100" };
  return { label: "Critical", color: "text-red-700", bgColor: "bg-red-100" };
}

function calculateMetricScore(value: number, config: HealthConfig | null): number | null {
  if (!config || config.normal_min === null || config.normal_max === null) return null;
  const { normal_min, normal_max, max_min, max_max } = config;
  const idealCenter = (normal_min + normal_max) / 2;
  const halfRange = (normal_max - normal_min) / 2 || 1;

  if (value >= normal_min && value <= normal_max) {
    const deviation = Math.abs(value - idealCenter);
    const score = 100 - (deviation / halfRange) * 30;
    return Math.max(70, Math.min(100, score));
  }

  if (max_min !== null && max_max !== null) {
    if (value < normal_min) {
      if (value < max_min) return Math.max(0, 25 - (max_min - value) * 10);
      const overshoot = normal_min - value;
      const tolerance = normal_min - max_min || 1;
      return Math.max(25, Math.min(69, 70 - (overshoot / tolerance) * 45));
    }
    if (value > max_max) return Math.max(0, 25 - (value - max_max) * 10);
    const overshoot = value - normal_max;
    const tolerance = max_max - normal_max || 1;
    return Math.max(25, Math.min(69, 70 - (overshoot / tolerance) * 45));
  }

  const deviation = value < normal_min ? normal_min - value : value - normal_max;
  return Math.max(25, Math.min(69, 70 - deviation * 10));
}

function ParameterEfficiencyCard({
  metric, 
  value, 
  healthConfig,
  onConfigure 
}: { 
  metric: string; 
  value: number; 
  healthConfig: HealthConfig | null;
  onConfigure: () => void;
}) {
  const fallbackRange = METRIC_RANGES[metric] || [0, 100];
  const min = healthConfig?.max_min ?? healthConfig?.normal_min ?? fallbackRange[0];
  const max = healthConfig?.max_max ?? healthConfig?.normal_max ?? fallbackRange[1];
  const denominator = Math.max(max - min, 1);
  const valuePct = Math.max(0, Math.min(100, ((value - min) / denominator) * 100));

  const normalMin = healthConfig?.normal_min ?? null;
  const normalMax = healthConfig?.normal_max ?? null;
  const hasNormalRange = normalMin !== null && normalMax !== null;
  const normalStartPct = hasNormalRange ? Math.max(0, Math.min(100, ((normalMin - min) / denominator) * 100)) : null;
  const normalEndPct = hasNormalRange ? Math.max(0, Math.min(100, ((normalMax - min) / denominator) * 100)) : null;
  const optimalMidPct = hasNormalRange ? Math.max(0, Math.min(100, ((((normalMin as number) + (normalMax as number)) / 2 - min) / denominator) * 100)) : null;

  const score = calculateMetricScore(value, healthConfig);
  const status = score !== null ? getEfficiencyStatus(score) : null;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <p className="text-[11px] uppercase tracking-[0.18em] font-semibold text-slate-500">
            {METRIC_LABELS[metric] || metric}
          </p>
          <p className="text-3xl font-bold text-slate-900 mt-2">
            {value.toFixed(2)}
            <span className="text-lg font-semibold text-slate-500 ml-1">{METRIC_UNITS[metric]?.trim() || ""}</span>
          </p>
        </div>
        <button
          onClick={onConfigure}
          className="text-xs font-medium px-2.5 py-1.5 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-100"
        >
          {healthConfig ? "Edit Range" : "Set Range"}
        </button>
      </div>

      <div className="relative h-3 rounded-full bg-slate-200 overflow-hidden">
        {hasNormalRange && normalStartPct !== null && normalEndPct !== null && (
          <div
            className="absolute top-0 h-full bg-emerald-100"
            style={{ left: `${Math.min(normalStartPct, normalEndPct)}%`, width: `${Math.abs(normalEndPct - normalStartPct)}%` }}
          />
        )}
        <div
          className="absolute left-0 top-0 h-full rounded-full transition-all duration-500"
          style={{
            width: `${valuePct}%`,
            background: "linear-gradient(90deg, #4f46e5 0%, #6366f1 70%, #818cf8 100%)",
          }}
        />
        {optimalMidPct !== null && (
          <div className="absolute top-[-4px] bottom-[-4px] w-[2px] bg-rose-500 rounded-full" style={{ left: `${optimalMidPct}%` }} />
        )}
      </div>

      <div className="mt-3 grid grid-cols-3 gap-3 text-xs text-slate-600">
        <p>Min: <span className="font-semibold text-slate-800">{min.toFixed(2)}</span></p>
        <p>Max: <span className="font-semibold text-slate-800">{max.toFixed(2)}</span></p>
        <p className="text-right">
          Optimal:{" "}
          <span className="font-semibold text-slate-800">
            {hasNormalRange ? `${(normalMin as number).toFixed(2)}-${(normalMax as number).toFixed(2)}` : "Not set"}
          </span>
        </p>
      </div>

      <div className="mt-3 flex items-center justify-between">
        {status ? (
          <div className={`text-xs px-2 py-1 rounded-full font-medium ${status.color} ${status.bgColor}`}>
            Efficiency {score?.toFixed(0)}% • {status.label}
          </div>
        ) : (
          <div className="text-xs px-2 py-1 rounded-full font-medium text-slate-600 bg-slate-100">
            Range not configured
          </div>
        )}
        {healthConfig && (
          <div className="text-xs text-slate-500">
            Weight: <span className="font-semibold text-slate-700">{healthConfig.weight}%</span>
          </div>
        )}
      </div>
    </div>
  );
}

function HealthConfigModal({ 
  isOpen, 
  onClose, 
  deviceId, 
  metric,
  existingConfig,
  allConfigs,
  onSave,
  onDelete 
}: { 
  isOpen: boolean; 
  onClose: () => void; 
  deviceId: string;
  metric: string;
  existingConfig: HealthConfig | null;
  allConfigs: HealthConfig[];
  onSave: (config: HealthConfigCreate) => void;
  onDelete: (configId: number) => void;
}) {
  const [formData, setFormData] = useState<HealthConfigCreate>({
    parameter_name: "",
    normal_min: undefined,
    normal_max: undefined,
    max_min: undefined,
    max_max: undefined,
    weight: 0,
    ignore_zero_value: false,
    is_active: true,
  });
  
  useEffect(() => {
    if (!metric) return;
    
    if (existingConfig) {
      setFormData({
        parameter_name: existingConfig.parameter_name,
        normal_min: existingConfig.normal_min ?? undefined,
        normal_max: existingConfig.normal_max ?? undefined,
        max_min: existingConfig.max_min ?? undefined,
        max_max: existingConfig.max_max ?? undefined,
        weight: existingConfig.weight,
        ignore_zero_value: existingConfig.ignore_zero_value,
        is_active: existingConfig.is_active,
      });
    } else {
      const defaultRanges: Record<string, { normal: [number, number]; max: [number, number] }> = {
        pressure: { normal: [2, 6], max: [0, 10] },
        temperature: { normal: [20, 60], max: [0, 100] },
        vibration: { normal: [0, 3], max: [0, 8] },
        power: { normal: [100, 400], max: [0, 500] },
        voltage: { normal: [210, 240], max: [180, 260] },
        current: { normal: [2, 15], max: [0, 20] },
        frequency: { normal: [48, 52], max: [40, 60] },
        power_factor: { normal: [0.85, 1.0], max: [0.5, 1.0] },
        speed: { normal: [1200, 1800], max: [800, 2200] },
        torque: { normal: [50, 300], max: [0, 500] },
        oil_pressure: { normal: [1, 4], max: [0, 5] },
        humidity: { normal: [30, 70], max: [0, 100] },
      };
      
      const defaults = defaultRanges[metric];
      
      setFormData({
        parameter_name: metric,
        normal_min: defaults?.normal[0] ?? undefined,
        normal_max: defaults?.normal[1] ?? undefined,
        max_min: defaults?.max[0] ?? undefined,
        max_max: defaults?.max[1] ?? undefined,
        weight: 0,
        ignore_zero_value: false,
        is_active: true,
      });
    }
  }, [metric, existingConfig]);
  
  if (!isOpen) return null;
  
  const totalWeight = allConfigs
    .filter(c => c.is_active && c.parameter_name !== metric)
    .reduce((sum, c) => sum + c.weight, 0) + formData.weight;
  
  const otherWeightsSum = allConfigs
    .filter(c => c.is_active && c.parameter_name !== metric)
    .reduce((sum, c) => sum + c.weight, 0);
  
  const remainingWeight = 100 - otherWeightsSum;
  const currentWeight = existingConfig?.weight || 0;
  const maxAllowedWeight = remainingWeight + currentWeight;
  const isWeightValid = Math.abs(totalWeight - 100) < 0.01;
  
  const handleWeightChange = (value: number) => {
    // Allow any value that's within the allowed range (remaining + current weight)
    // This allows decreasing weight when editing
    if (!isNaN(value) && value >= 0 && value <= maxAllowedWeight) {
      setFormData({ ...formData, weight: value });
    }
  };
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">Configure Health: {metric}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">✕</button>
        </div>
        
        <div className="space-y-4">
          <div className="p-3 bg-blue-50 rounded text-sm">
            <p className="font-medium text-blue-800 mb-2">Normal Range (Optimal)</p>
            <p className="text-blue-600 text-xs">Values here get 70-100% efficiency score</p>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Normal Min</label>
              <input type="number" step="0.1" value={formData.normal_min ?? ""} onChange={(e) => setFormData({ ...formData, normal_min: e.target.value ? parseFloat(e.target.value) : undefined })} className="w-full px-3 py-2 border rounded-md" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Normal Max</label>
              <input type="number" step="0.1" value={formData.normal_max ?? ""} onChange={(e) => setFormData({ ...formData, normal_max: e.target.value ? parseFloat(e.target.value) : undefined })} className="w-full px-3 py-2 border rounded-md" />
            </div>
          </div>
          
          <div className="p-3 bg-orange-50 rounded text-sm">
            <p className="font-medium text-orange-800 mb-2">Maximum Range (Limits)</p>
            <p className="text-orange-600 text-xs">Values outside normal but within max get 25-69% score</p>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Max Min</label>
              <input type="number" step="0.1" value={formData.max_min ?? ""} onChange={(e) => setFormData({ ...formData, max_min: e.target.value ? parseFloat(e.target.value) : undefined })} className="w-full px-3 py-2 border rounded-md" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Max Max</label>
              <input type="number" step="0.1" value={formData.max_max ?? ""} onChange={(e) => setFormData({ ...formData, max_max: e.target.value ? parseFloat(e.target.value) : undefined })} className="w-full px-3 py-2 border rounded-md" />
            </div>
          </div>
          
            <div className="border-t pt-4">
              <label className="block text-sm font-medium mb-1">
                Weight (%) 
                {existingConfig && <span className="text-xs text-slate-500 font-normal ml-2">(Saved: {currentWeight}%, Max: {maxAllowedWeight}%)</span>}
              </label>
              <input 
                type="number" 
                min="0" 
                max={maxAllowedWeight}
                step="1" 
                value={formData.weight} 
                onChange={(e) => handleWeightChange(parseFloat(e.target.value) || 0)}
                className="w-full px-3 py-2 border rounded-md" 
              />
              <div className={`text-xs mt-2 p-2 rounded ${isWeightValid ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
                <p>Total Weight: <strong>{totalWeight.toFixed(1)}%</strong> / 100%</p>
                <p>Remaining: <strong>{remainingWeight.toFixed(1)}%</strong></p>
                {!isWeightValid && <p className="mt-1">⚠️ Total must equal 100% to calculate health score</p>}
                {isWeightValid && <p className="mt-1">✓ Weight configured correctly</p>}
              </div>
            </div>
          
          <div className="flex items-center gap-2">
            <input type="checkbox" id="ignoreZero" checked={formData.ignore_zero_value} onChange={(e) => setFormData({ ...formData, ignore_zero_value: e.target.checked })} className="rounded" />
            <label htmlFor="ignoreZero" className="text-sm">Ignore zero values (exclude from scoring when machine is off)</label>
          </div>
          
          {existingConfig && (
            <Button variant="danger" className="w-full" onClick={() => onDelete(existingConfig.id)}>
              Delete Configuration
            </Button>
          )}
        </div>
        
        <div className="flex gap-2 mt-6">
          <Button variant="outline" onClick={onClose} className="flex-1">Cancel</Button>
          <Button onClick={() => onSave(formData)} className="flex-1">
            {isWeightValid ? "Save" : `Save (${totalWeight.toFixed(0)}%)`}
          </Button>
        </div>
        {!isWeightValid && (
          <p className="text-xs text-center mt-2 text-amber-600">
            ⚠️ Note: Health score will only calculate when total weight = 100%
          </p>
        )}
      </div>
    </div>
  );
}

export default function MachineDashboardPage() {
  const params = useParams();
  const deviceId = (params.deviceId as string) || "";

  const [machine, setMachine] = useState<Device | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryPoint[]>([]);
  const [telemetryStreamRows, setTelemetryStreamRows] = useState<TelemetryPoint[]>([]);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [uptime, setUptime] = useState<UptimeData | null>(null);
  const [healthConfigs, setHealthConfigs] = useState<HealthConfig[]>([]);
  const [healthScore, setHealthScore] = useState<HealthScore | null>(null);
  const [currentState, setCurrentState] = useState<CurrentState | null>(null);
  const [idleStats, setIdleStats] = useState<IdleStats | null>(null);
  const [idleThresholdInput, setIdleThresholdInput] = useState<string>("");
  const [idleSaveMessage, setIdleSaveMessage] = useState<string>("");
  const [idleSaving, setIdleSaving] = useState(false);
  const [widgetConfig, setWidgetConfig] = useState<DashboardWidgetConfig | null>(null);
  const [selectedWidgetFields, setSelectedWidgetFields] = useState<string[]>([]);
  const [widgetSaveMessage, setWidgetSaveMessage] = useState<string>("");
  const [widgetSaving, setWidgetSaving] = useState(false);
  const [widgetDirty, setWidgetDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "telemetry" | "parameters" | "rules">("overview");
  const [showAddShift, setShowAddShift] = useState(false);
  const [showHealthConfig, setShowHealthConfig] = useState(false);
  const [selectedMetric, setSelectedMetric] = useState<string>("");
  const [showAlertHistory, setShowAlertHistory] = useState(false);
  const [activityEvents, setActivityEvents] = useState<ActivityEvent[]>([]);
  const [unreadEventCount, setUnreadEventCount] = useState(0);
  const [trendMetric, setTrendMetric] = useState<PerformanceTrendMetric>("health");
  const [trendRange, setTrendRange] = useState<PerformanceTrendRange>("1h");
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendError, setTrendError] = useState<string | null>(null);
  const [trendData, setTrendData] = useState<PerformanceTrendData | null>(null);
  const [newShift, setNewShift] = useState<ShiftCreate>({
    shift_name: "", shift_start: "09:00", shift_end: "17:00", maintenance_break_minutes: 0, day_of_week: null, is_active: true,
  });
  const pollingInterval = useRef<NodeJS.Timeout | null>(null);
  const telemetryPollingInterval = useRef<NodeJS.Timeout | null>(null);
  const currentStatePollingInterval = useRef<NodeJS.Timeout | null>(null);
  const idleStatsPollingInterval = useRef<NodeJS.Timeout | null>(null);
  const latestTelemetryTimestampRef = useRef<string | null>(null);

  const fetchData = async (isInitial = false) => {
    try {
      const [machineData, telemetryData, uptimeData, shiftsData, healthConfigsData, widgetConfigData] = await Promise.all([
        getDeviceById(deviceId),
        getTelemetry(deviceId, { limit: "100" }),
        getUptime(deviceId),
        getShifts(deviceId),
        getHealthConfigs(deviceId),
        getDashboardWidgetConfig(deviceId),
      ]);
      // Always update machine data to get latest last_seen_timestamp
      setMachine(machineData);
      const ascTelemetry = sortTelemetryAsc(telemetryData);
      const descTelemetry = sortTelemetryDesc(telemetryData);
      setTelemetry(ascTelemetry);
      setTelemetryStreamRows(descTelemetry.slice(0, 10));
      latestTelemetryTimestampRef.current = descTelemetry[0]?.timestamp || null;
      setUptime(uptimeData);
      setShifts(shiftsData);
      setHealthConfigs(healthConfigsData);
      setWidgetConfig(widgetConfigData);
      if (isInitial || !widgetDirty) {
        setSelectedWidgetFields(widgetConfigData.effective_fields || []);
        setWidgetDirty(false);
      }
      
      const latest = ascTelemetry.at(-1);
      if (latest && healthConfigsData.length > 0) {
        const telemetryValues: TelemetryValues = {
          values: {},
          machine_state: "RUNNING",
        };
        
        for (const [key, val] of Object.entries(latest)) {
          if (typeof val === 'number') {
            telemetryValues.values[key] = val;
          }
        }
        
        try {
          const score = await calculateHealthScore(deviceId, telemetryValues);
          setHealthScore(score);
        } catch (e) {
          console.error("Health score error:", e);
        }
      }
      
      setError(null);
    } catch (err) {
      if (isInitial) setError(err instanceof Error ? err.message : "Failed to fetch data");
    } finally {
      if (isInitial) setLoading(false);
    }
  };

  const pollLatestTelemetry = async () => {
    try {
      const latestBatch = await getTelemetry(deviceId, { limit: "1" });
      if (latestBatch.length === 0) return;
      const latest = latestBatch[0];
      if (!latest?.timestamp) return;
      if (latestTelemetryTimestampRef.current === latest.timestamp) return;
      latestTelemetryTimestampRef.current = latest.timestamp;

      setTelemetryStreamRows((prev) => {
        const next = [latest, ...prev.filter((p) => p.timestamp !== latest.timestamp)];
        return sortTelemetryDesc(next).slice(0, 10);
      });

      // Keep local chart/history data bounded and ordered.
      setTelemetry((prev) => {
        const next = [...prev.filter((p) => p.timestamp !== latest.timestamp), latest];
        return sortTelemetryAsc(next).slice(-100);
      });
    } catch (err) {
      console.error("Telemetry stream poll failed:", err);
    }
  };

  const loadActivityHistory = async () => {
    try {
      const [eventsResult, unreadCount] = await Promise.all([
        getActivityEvents({ deviceId, page: 1, pageSize: 25 }),
        getActivityUnreadCount(deviceId),
      ]);
      setActivityEvents(eventsResult.data);
      setUnreadEventCount(unreadCount);
    } catch (err) {
      console.error("Failed to load activity history:", err);
    }
  };

  const loadPerformanceTrends = async () => {
    try {
      setTrendLoading(true);
      setTrendError(null);
      const data = await getPerformanceTrends(deviceId, trendMetric, trendRange);
      setTrendData(data);
    } catch (err) {
      setTrendError(err instanceof Error ? err.message : "Failed to load performance trends");
    } finally {
      setTrendLoading(false);
    }
  };

  const loadIdleConfig = async () => {
    try {
      const config = await getIdleConfig(deviceId);
      setIdleThresholdInput(
        config.idle_current_threshold !== null && config.idle_current_threshold !== undefined
          ? String(config.idle_current_threshold)
          : ""
      );
    } catch (err) {
      console.error("Failed to load idle config:", err);
    }
  };

  const loadCurrentState = async () => {
    try {
      const state = await getCurrentState(deviceId);
      setCurrentState(state);
    } catch (err) {
      console.error("Failed to load current state:", err);
    }
  };

  const loadIdleStats = async () => {
    try {
      const stats = await getIdleStats(deviceId);
      setIdleStats(stats);
    } catch (err) {
      console.error("Failed to load idle stats:", err);
    }
  };

  useEffect(() => {
    if (!deviceId) return;
    fetchData(true);
    loadIdleConfig();
    loadCurrentState();
    loadIdleStats();
    pollingInterval.current = setInterval(() => fetchData(false), 10000);
    telemetryPollingInterval.current = setInterval(() => pollLatestTelemetry(), 1000);
    currentStatePollingInterval.current = setInterval(() => loadCurrentState(), 30000);
    idleStatsPollingInterval.current = setInterval(() => loadIdleStats(), 60000);
    return () => {
      if (pollingInterval.current) clearInterval(pollingInterval.current);
      if (telemetryPollingInterval.current) clearInterval(telemetryPollingInterval.current);
      if (currentStatePollingInterval.current) clearInterval(currentStatePollingInterval.current);
      if (idleStatsPollingInterval.current) clearInterval(idleStatsPollingInterval.current);
    };
  }, [deviceId]);

  useEffect(() => {
    if (!deviceId) return;
    loadActivityHistory();
    const timer = setInterval(() => loadActivityHistory(), 5000);
    return () => clearInterval(timer);
  }, [deviceId]);

  useEffect(() => {
    if (!deviceId) return;
    loadPerformanceTrends();
  }, [deviceId, trendMetric, trendRange]);

  const handleAddShift = async () => {
    if (newShift.shift_start === newShift.shift_end) {
      alert("Shift start and end times cannot be the same.");
      return;
    }
    if (shiftOverlapConflicts.length > 0) {
      alert("Shift overlaps with existing shifts. Please pick a non-overlapping time.");
      return;
    }
    try {
      await createShift(deviceId, newShift);
      setShowAddShift(false);
      setNewShift({ shift_name: "", shift_start: "09:00", shift_end: "17:00", maintenance_break_minutes: 0, day_of_week: null, is_active: true });
      fetchData(false);
    } catch (err) { alert("Failed: " + (err as Error).message); }
  };

  const handleDeleteShift = async (shiftId: number) => {
    if (!confirm("Delete this shift?")) return;
    try { await deleteShift(deviceId, shiftId); fetchData(false); } catch (err) { alert("Failed: " + (err as Error).message); }
  };

  const handleSaveHealthConfig = async (config: HealthConfigCreate) => {
    try {
      const existing = healthConfigs.find(c => c.parameter_name === config.parameter_name);
      if (existing) {
        await updateHealthConfig(deviceId, existing.id, config);
      } else {
        await createHealthConfig(deviceId, config);
      }
      setShowHealthConfig(false);
      setSelectedMetric("");
      fetchData(false);
    } catch (err) { alert("Failed: " + (err as Error).message); }
  };

  const handleDeleteHealthConfig = async (configId: number) => {
    try {
      await deleteHealthConfig(deviceId, configId);
      setShowHealthConfig(false);
      setSelectedMetric("");
      fetchData(false);
    } catch (err) { alert("Failed: " + (err as Error).message); }
  };

  const handleSaveIdleThreshold = async () => {
    const parsed = Number(idleThresholdInput);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      alert("Idle threshold must be a positive number.");
      return;
    }
    try {
      setIdleSaving(true);
      setIdleSaveMessage("");
      await saveIdleConfig(deviceId, parsed);
      await Promise.all([loadIdleConfig(), loadCurrentState(), loadIdleStats()]);
      setIdleSaveMessage(`Saved: Below ${parsed.toFixed(2)}A = Idle Running`);
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    } finally {
      setIdleSaving(false);
    }
  };

  const handleToggleWidgetField = (field: string) => {
    setWidgetSaveMessage("");
    setWidgetDirty(true);
    setSelectedWidgetFields((prev) => {
      if (prev.includes(field)) {
        return prev.filter((f) => f !== field);
      }
      return [...prev, field];
    });
  };

  const handleSaveWidgetConfig = async () => {
    try {
      setWidgetSaving(true);
      setWidgetSaveMessage("");
      const saved = await saveDashboardWidgetConfig(deviceId, selectedWidgetFields);
      setWidgetConfig(saved);
      setSelectedWidgetFields(saved.effective_fields || []);
      setWidgetSaveMessage("Widget configuration saved.");
      setWidgetDirty(false);
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    } finally {
      setWidgetSaving(false);
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await markAllActivityRead(deviceId);
      await loadActivityHistory();
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    }
  };

  const handleClearHistory = async () => {
    if (!confirm("Clear all alert history for this machine?")) return;
    try {
      await clearActivityHistory(deviceId);
      await loadActivityHistory();
    } catch (err) {
      alert("Failed: " + (err as Error).message);
    }
  };

  if (loading) return <div className="p-8"><div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div></div></div>;
  if (error || !machine) return <div className="p-8"><div className="bg-red-50 border p-6 rounded"><h2 className="text-red-800 font-semibold">Error</h2><p className="text-red-600">{error || "Not found"}</p><Link href="/machines"><Button className="mt-4">Back</Button></Link></div></div>;

  const latestTelemetry = telemetry.at(-1);
  const dynamicMetrics = getDynamicMetrics(telemetry);
  const effectiveWidgetFields = widgetConfig?.effective_fields || dynamicMetrics;
  const selectedWidgetFieldSet = new Set(selectedWidgetFields);
  const visibleOverviewMetrics = effectiveWidgetFields.filter((field) => dynamicMetrics.includes(field));
  const healthPercent = typeof healthScore?.health_score === "number" ? healthScore.health_score : null;
  const uptimePercent = typeof uptime?.uptime_percentage === "number" ? uptime.uptime_percentage : null;
  const performanceChartData = (trendData?.points || [])
    .map((point) => ({
      timestamp: point.timestamp,
      value: trendMetric === "health" ? point.health_score : point.uptime_percentage,
    }))
    .filter((p) => typeof p.value === "number") as Array<{ timestamp: string; value: number }>;
  const shiftTimeEqual = newShift.shift_start === newShift.shift_end;
  const shiftOverlapConflicts = findOverlapConflicts(newShift, shifts);
  const shiftFormError = shiftTimeEqual
    ? "Start and end cannot be the same."
    : shiftOverlapConflicts.length > 0
      ? `Overlaps with: ${shiftOverlapConflicts
          .map((s) => `${s.shift_name} (${formatShiftRange(s.shift_start, s.shift_end)})`)
          .join(", ")}`
      : "";
  const shiftFormBlocked = !newShift.shift_name || shiftTimeEqual || shiftOverlapConflicts.length > 0;

  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <div className="flex items-center gap-2 text-sm text-slate-500 mb-4">
            <Link href="/machines" className="hover:text-slate-900">Machines</Link><span>/</span><span className="text-slate-900">{machine.name}</span>
          </div>
          <div className="relative rounded-3xl border border-slate-200 bg-gradient-to-b from-white to-slate-50/70 p-6 md:p-8 shadow-sm">
            <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-6">
              <div>
                <h1 className="text-4xl font-bold tracking-tight text-slate-900">{machine.name}</h1>
                <p className="text-slate-500 font-mono mt-1 text-lg">{machine.id}</p>
                {machine.last_seen_timestamp ? (
                  <p className="text-sm text-slate-500 mt-2">
                    Last seen: {formatIST(machine.last_seen_timestamp)}
                  </p>
                ) : (
                  <p className="text-sm text-slate-500 mt-2">Last seen: No data received</p>
                )}
              </div>

              <div className="flex items-center gap-3 self-start">
                <button
                  type="button"
                  onClick={() => setShowAlertHistory((prev) => !prev)}
                  className="relative inline-flex items-center justify-center w-11 h-11 rounded-xl border border-slate-200 bg-white hover:bg-slate-50"
                  title="Machine alert history"
                >
                  <svg className="w-5 h-5 text-slate-700" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2a2 2 0 0 1-.6 1.4L4 17h5" />
                    <path d="M10 17a2 2 0 0 0 4 0" />
                  </svg>
                  {unreadEventCount > 0 && (
                    <span className="absolute -top-1 -right-1 min-w-5 h-5 px-1 rounded-full bg-red-600 text-white text-[10px] leading-5 text-center">
                      {unreadEventCount > 99 ? "99+" : unreadEventCount}
                    </span>
                  )}
                </button>
                <StatusBadge status={machine.runtime_status} />
                <LoadStateBadge
                  state={getEffectiveLoadState(machine.runtime_status, currentState?.state)}
                />
              </div>
            </div>

            <div className="mt-7 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Name</p>
                <p className="text-xl font-semibold text-slate-900 mt-2">{machine.name}</p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Status</p>
                <p className={`text-2xl font-bold mt-2 ${machine.runtime_status === "running" ? "text-emerald-500" : "text-rose-500"}`}>
                  {machine.runtime_status === "running" ? "Running" : "Stopped"}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">ID</p>
                <p className="text-lg font-semibold text-slate-800 mt-2 font-mono">{machine.id}</p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Type</p>
                <p className="text-xl font-semibold text-slate-900 mt-2 capitalize">{machine.type || "—"}</p>
              </div>
              <div className="relative group rounded-xl border border-slate-200 bg-white p-4 cursor-help">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Uptime</p>
                <p className="text-3xl font-bold text-slate-900 mt-2">{uptimePercent !== null ? `${uptimePercent.toFixed(1)}%` : "—"}</p>
                <p className="text-[11px] text-slate-500 mt-1">
                  {uptimePercent !== null ? "Hover for calc details" : (uptime?.message || "No active shift window")}
                </p>
                <div className="pointer-events-none absolute left-1/2 top-full z-30 mt-2 w-72 -translate-x-1/2 rounded-xl border border-slate-200 bg-white p-3 shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all">
                  <p className="text-xs font-semibold text-slate-700 mb-2">Uptime Calculation</p>
                  {uptime ? (
                    <>
                      <p className="text-xs text-slate-600">Active shifts: <span className="font-medium">{uptime.shifts_configured}</span></p>
                      {uptime.uptime_percentage === null ? (
                        <p className="text-xs text-amber-700 mt-1">{uptime.message || "No active shift window right now."}</p>
                      ) : (
                        <>
                          <p className="text-xs text-slate-600">Planned duration: <span className="font-medium">{formatMinutes(uptime.total_planned_minutes)}</span></p>
                          <p className="text-xs text-slate-600">Effective duration: <span className="font-medium">{formatMinutes(uptime.total_effective_minutes)}</span></p>
                          <p className="text-xs text-slate-600">Actual running: <span className="font-medium">{formatMinutes(uptime.actual_running_minutes)}</span></p>
                          <p className="text-xs text-slate-600">Data coverage: <span className="font-medium">{typeof uptime.data_coverage_pct === "number" ? `${uptime.data_coverage_pct.toFixed(1)}%` : "—"}</span></p>
                          <p className="text-xs text-slate-600">Data quality: <span className="font-medium capitalize">{uptime.data_quality || "—"}</span></p>
                          {uptime.window_start && uptime.window_end && (
                            <p className="text-xs text-slate-600">
                              Shift window: <span className="font-medium">{formatIST(uptime.window_start, "—")} → {formatIST(uptime.window_end, "—")}</span>
                            </p>
                          )}
                          <p className="text-xs text-slate-500 mt-2">Formula: uptime = running minutes / effective shift minutes.</p>
                        </>
                      )}
                    </>
                  ) : (
                    <p className="text-xs text-slate-500">No shift configuration found.</p>
                  )}
                </div>
              </div>
              <div className="relative group rounded-xl border border-slate-200 bg-white p-4 cursor-help">
                <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Health Score</p>
                <p className={`text-5xl font-extrabold mt-1 ${healthPercent !== null && healthPercent >= 70 ? "text-emerald-400" : healthPercent !== null ? "text-orange-500" : "text-slate-400"}`}>
                  {healthPercent !== null ? `${healthPercent.toFixed(0)}%` : "—"}
                </p>
                <p className="text-[11px] text-slate-500 mt-1">Hover for calc details</p>
                <div className="pointer-events-none absolute right-0 top-full z-30 mt-2 w-80 rounded-xl border border-slate-200 bg-white p-3 shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all">
                  <p className="text-xs font-semibold text-slate-700 mb-2">Health Score Breakdown</p>
                  {healthScore ? (
                    <>
                      <p className="text-xs text-slate-600">Status: <span className="font-medium">{healthScore.status}</span></p>
                      <p className="text-xs text-slate-600">Machine state: <span className="font-medium">{healthScore.machine_state}</span></p>
                      <p className="text-xs text-slate-600">Parameters used: <span className="font-medium">{healthScore.parameters_included}</span>, skipped: <span className="font-medium">{healthScore.parameters_skipped}</span></p>
                      <p className="text-xs text-slate-600">Configured weight total: <span className="font-medium">{healthScore.total_weight_configured}%</span></p>
                      <p className="text-xs text-slate-500 mt-2">Health = weighted average of configured parameter scores (0-100).</p>
                      <div className="mt-2 border-t border-slate-100 pt-2 space-y-1">
                        {healthScore.parameter_scores.slice(0, 4).map((p) => (
                          <p key={p.parameter_name} className="text-xs text-slate-600">
                            {p.parameter_name}: {p.raw_score.toFixed(1)}% ({p.weight}% wt)
                          </p>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="text-xs text-slate-500">No health data available.</p>
                  )}
                </div>
              </div>
            </div>

            <div className="mt-4 text-sm text-slate-600">
              <span className="font-medium text-slate-700">Location:</span> {machine.location || "—"}
            </div>

            <div className="mt-5 rounded-2xl border border-slate-200 bg-white p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Idle Running Waste</p>
              {!idleStats?.threshold_configured ? (
                <p className="text-sm text-slate-600 mt-2">
                  Set idle threshold in Parameter Configuration to enable this widget.
                </p>
              ) : (
                <>
                  <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div className="rounded-xl border border-slate-200 p-3">
                      <p className="text-xs text-slate-500 uppercase tracking-wide">Today Idle Time</p>
                      <p className="text-lg font-semibold text-slate-900 mt-1">{idleStats?.today?.idle_duration_label || "0 min"}</p>
                    </div>
                    <div className="rounded-xl border border-slate-200 p-3">
                      <p className="text-xs text-slate-500 uppercase tracking-wide">Today Idle Cost</p>
                      <p className="text-lg font-semibold text-slate-900 mt-1">
                        {idleStats?.tariff_configured
                          ? `${idleStats.today?.currency || "INR"} ${Number(idleStats.today?.idle_cost || 0).toFixed(2)}`
                          : "Set tariff in Settings"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-slate-200 p-3">
                      <p className="text-xs text-slate-500 uppercase tracking-wide">Monthly Idle Cost</p>
                      <p className="text-lg font-semibold text-slate-900 mt-1">
                        {idleStats?.tariff_configured
                          ? `${idleStats.month?.currency || "INR"} ${Number(idleStats.month?.idle_cost || 0).toFixed(2)}`
                          : "Set tariff in Settings"}
                      </p>
                    </div>
                  </div>
                  {idleStats?.pf_estimated && (
                    <p className="text-xs text-amber-700 mt-3">
                      Power factor unavailable in telemetry for part of the period. Cost estimated with PF = 1.0.
                    </p>
                  )}
                </>
              )}
            </div>

            {showAlertHistory && (
              <div className="absolute right-6 top-16 z-40 w-[460px] max-h-[520px] bg-white border border-slate-200 shadow-xl rounded-xl overflow-hidden">
                <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">Machine Alerts</p>
                    <p className="text-xs text-slate-500">{machine.id}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowAlertHistory(false)}
                    className="text-slate-400 hover:text-slate-700"
                  >
                    ✕
                  </button>
                </div>
                <div className="max-h-[380px] overflow-y-auto p-3 space-y-3">
                  {activityEvents.length === 0 ? (
                    <div className="text-center text-sm text-slate-500 py-8">No alert history</div>
                  ) : (
                    activityEvents.map((event) => (
                      <div key={event.eventId} className={`rounded-lg border p-3 ${event.isRead ? "bg-slate-50 border-slate-200" : "bg-red-50 border-red-200"}`}>
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-sm font-semibold text-slate-900">{event.title}</p>
                          <span className="text-[11px] px-2 py-0.5 rounded bg-slate-100 text-slate-700">
                            {formatEventType(event.eventType)}
                          </span>
                        </div>
                        <p className="text-xs text-slate-600 mt-1">{event.message}</p>
                        <p className="text-[11px] text-slate-500 mt-2">{formatTimestamp(event.createdAt)}</p>
                      </div>
                    ))
                  )}
                </div>
                <div className="px-4 py-3 border-t border-slate-200 flex items-center justify-between gap-2">
                  <Button variant="outline" size="sm" onClick={handleMarkAllRead}>Mark all read</Button>
                  <Button variant="danger" size="sm" onClick={handleClearHistory}>Clear history</Button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="border-b border-slate-200 mb-6">
          <nav className="flex gap-8">
            {[{ id: "overview", label: "Overview" }, { id: "telemetry", label: "Telemetry" }, { id: "parameters", label: "Parameter Configuration" }, { id: "rules", label: "Configure Rules" }].map((tab) => (
              <button key={tab.id} onClick={() => setActiveTab(tab.id as any)}
                className={`pb-4 text-sm font-medium border-b-2 ${activeTab === tab.id ? "border-blue-600 text-blue-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>
                {tab.label}
              </button>
            ))}
          </nav>
        </div>

        {activeTab === "overview" && (
          <div className="space-y-6">
            {visibleOverviewMetrics.length > 0 && (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                {visibleOverviewMetrics.map((metric) => {
                  const value = (latestTelemetry as any)[metric];
                  if (typeof value !== 'number') return null;
                  return (
                    <ParameterEfficiencyCard
                      key={metric}
                      metric={metric}
                      value={value}
                      healthConfig={healthConfigs.find(c => c.parameter_name === metric) || null}
                      onConfigure={() => { setSelectedMetric(metric); setShowHealthConfig(true); }}
                    />
                  );
                })}
              </div>
            )}

            {telemetry.length > 0 && visibleOverviewMetrics.length > 0 && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {visibleOverviewMetrics.map((metric) => {
                  const data = getMetricData(telemetry, metric);
                  if (data.length === 0) return null;
                  return <Card key={metric}><CardHeader><CardTitle>{METRIC_LABELS[metric] || metric} Trend</CardTitle></CardHeader><CardContent><TimeSeriesChart data={data} color={METRIC_COLORS[metric] || "#2563eb"} unit={METRIC_UNITS[metric] || ""} /></CardContent></Card>;
                })}
              </div>
            )}

            <Card>
              <CardHeader className="flex flex-row items-center justify-between gap-4">
                <div>
                  <CardTitle>Performance Trends</CardTitle>
                  <p className="text-sm text-slate-500 mt-1">Recent telemetry-derived {trendMetric} trend</p>
                </div>
                <div className="flex items-center gap-2 flex-wrap justify-end">
                  <div className="inline-flex rounded-lg border border-slate-200 p-1">
                    {([
                      { value: "health", label: "Health" },
                      { value: "uptime", label: "Uptime" },
                    ] as { value: PerformanceTrendMetric; label: string }[]).map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        onClick={() => setTrendMetric(item.value)}
                        className={`px-3 py-1.5 text-sm rounded-md ${
                          trendMetric === item.value
                            ? "bg-blue-600 text-white"
                            : "text-slate-600 hover:bg-slate-100"
                        }`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                  <div className="inline-flex rounded-lg border border-slate-200 p-1">
                    {TREND_RANGE_OPTIONS.map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        onClick={() => setTrendRange(item.value)}
                        className={`px-2.5 py-1.5 text-sm rounded-md ${
                          trendRange === item.value
                            ? "bg-slate-800 text-white"
                            : "text-slate-600 hover:bg-slate-100"
                        }`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {trendLoading ? (
                  <div className="h-64 flex items-center justify-center text-slate-500">Loading trends...</div>
                ) : trendError ? (
                  <div className="h-64 flex items-center justify-center text-red-600">{trendError}</div>
                ) : performanceChartData.length === 0 ? (
                  <div className="h-64 flex flex-col items-center justify-center text-slate-500">
                    <p>No {trendMetric} trend data available.</p>
                    <p className="text-sm mt-1">{trendData?.message || "Configure health/shift settings and wait for trend snapshots."}</p>
                  </div>
                ) : (
                  <TimeSeriesChart
                    data={performanceChartData}
                    color={trendMetric === "health" ? "#10b981" : "#2563eb"}
                    unit="%"
                    showArea
                    title={`${trendMetric === "health" ? "Health Score" : "Uptime"} (${trendRange})`}
                  />
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {activeTab === "telemetry" && (
          <div className="space-y-6">
            {telemetryStreamRows.length === 0 ? <Card><CardContent className="py-12 text-center text-slate-500">No data</CardContent></Card> : (
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>Recent Telemetry</CardTitle>
                  <span className="text-xs text-slate-400">Auto-refresh every 1s • {telemetryStreamRows.length} records</span>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-slate-200">
                      <thead className="bg-slate-50"><tr>
                        <th className="px-6 py-3 text-left text-xs font-medium text-slate-500">Timestamp</th>
                        {dynamicMetrics.map((m) => <th key={m} className="px-6 py-3 text-left text-xs font-medium text-slate-500">{METRIC_LABELS[m] || m}</th>)}
                      </tr></thead>
                      <tbody className="bg-white divide-y">
                        {telemetryStreamRows.map((point, i) => (
                          <tr key={i} className={i === 0 ? "bg-blue-50" : ""}>
                            <td className="px-6 py-3 text-sm font-mono">{formatTimestamp(point.timestamp)}</td>
                            {dynamicMetrics.map((m) => <td key={m} className="px-6 py-3 text-sm">{(point as any)[m]?.toFixed(2) ?? "—"}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        )}

        {activeTab === "parameters" && (
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>Telemetry Widgets</CardTitle>
                <p className="text-sm text-slate-600">
                  Select telemetry widgets to show on this machine dashboard.
                </p>
              </CardHeader>
              <CardContent>
                {widgetConfig && widgetConfig.available_fields.length > 0 ? (
                  <>
                    <div className="flex flex-wrap gap-3">
                      {widgetConfig.available_fields.map((field) => {
                        const selected = selectedWidgetFieldSet.has(field);
                        return (
                          <button
                            key={field}
                            type="button"
                            onClick={() => handleToggleWidgetField(field)}
                            className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition ${
                              selected
                                ? "border-blue-300 bg-blue-50 text-blue-700"
                                : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                            }`}
                          >
                            <span className={`inline-flex h-4 w-4 items-center justify-center rounded border text-xs ${
                              selected ? "border-blue-500 bg-blue-600 text-white" : "border-slate-400 text-transparent"
                            }`}>
                              ✓
                            </span>
                            <span
                              className="h-2.5 w-2.5 rounded-full"
                              style={{ backgroundColor: METRIC_COLORS[field] || "#64748b" }}
                            />
                            {METRIC_LABELS[field] || field}
                          </button>
                        );
                      })}
                    </div>
                    <div className="mt-4 flex items-center gap-3">
                      <Button onClick={handleSaveWidgetConfig} disabled={widgetSaving || !widgetDirty}>
                        {widgetSaving ? "Saving..." : "Save Widgets"}
                      </Button>
                      {widgetDirty && (
                        <p className="text-xs text-amber-700">
                          Unsaved changes
                        </p>
                      )}
                      {widgetConfig.default_applied && (
                        <p className="text-xs text-slate-500">
                          Default mode active: all discovered widgets are shown until you save.
                        </p>
                      )}
                      {widgetSaveMessage && (
                        <p className="text-xs text-emerald-700">{widgetSaveMessage}</p>
                      )}
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-slate-500">
                    No numeric telemetry fields discovered yet. Start telemetry to configure widgets.
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle>Shift Configuration</CardTitle>
                <Button onClick={() => setShowAddShift(!showAddShift)}>{showAddShift ? "Cancel" : "+ Add Shift"}</Button>
              </CardHeader>
              <CardContent>
                {showAddShift && (
                  <div className="bg-slate-50 p-4 rounded-lg mb-6 space-y-4">
                    <p className="text-xs text-slate-600">
                      Rule: overlaps are not allowed. Touching boundaries are allowed (for example, 09:00-10:00 and 10:00-11:00). Overnight shifts are shown as <span className="font-semibold">(+1 day)</span>.
                    </p>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div><label className="block text-sm font-medium mb-1">Shift Name</label><input type="text" value={newShift.shift_name} onChange={(e) => setNewShift({ ...newShift, shift_name: e.target.value })} placeholder="e.g., Morning Shift" className="w-full px-3 py-2 border rounded-md" /></div>
                      <div><label className="block text-sm font-medium mb-1">Day of Week</label><select value={newShift.day_of_week ?? ""} onChange={(e) => setNewShift({ ...newShift, day_of_week: e.target.value ? parseInt(e.target.value) : null })} className="w-full px-3 py-2 border rounded-md">{DAYS_OF_WEEK.map(d => <option key={d.value ?? "all"} value={d.value ?? ""}>{d.label}</option>)}</select></div>
                      <div><label className="block text-sm font-medium mb-1">Start Time</label><input type="time" value={newShift.shift_start} onChange={(e) => setNewShift({ ...newShift, shift_start: e.target.value })} className="w-full px-3 py-2 border rounded-md" /></div>
                      <div><label className="block text-sm font-medium mb-1">End Time</label><input type="time" value={newShift.shift_end} onChange={(e) => setNewShift({ ...newShift, shift_end: e.target.value })} className="w-full px-3 py-2 border rounded-md" /></div>
                      <div><label className="block text-sm font-medium mb-1">Maintenance Break (min)</label><input type="number" min="0" max="480" value={newShift.maintenance_break_minutes} onChange={(e) => setNewShift({ ...newShift, maintenance_break_minutes: parseInt(e.target.value) || 0 })} className="w-full px-3 py-2 border rounded-md" /></div>
                    </div>
                    {shiftFormError && (
                      <p className="text-sm text-red-600">{shiftFormError}</p>
                    )}
                    <Button onClick={handleAddShift} disabled={shiftFormBlocked}>Save Shift</Button>
                  </div>
                )}
                {shifts.length === 0 ? <div className="text-center py-8 text-slate-500">No shifts configured</div> : (
                  <div className="space-y-4">
                    {shifts.map((shift) => (
                      <div key={shift.id} className={`flex items-center justify-between p-4 rounded-lg border ${shift.is_active ? "bg-white" : "bg-slate-50 opacity-60"}`}>
                        <div>
                          <div className="flex items-center gap-2"><h3 className="font-medium">{shift.shift_name}</h3>{!shift.is_active && <span className="text-xs bg-slate-200 px-2 py-0.5 rounded">Inactive</span>}</div>
                          <p className="text-sm text-slate-500 mt-1">
                            {formatShiftRange(shift.shift_start, shift.shift_end)}
                            {isOvernightRange(shift.shift_start, shift.shift_end) && (
                              <span className="ml-2 inline-flex rounded bg-indigo-100 px-2 py-0.5 text-[11px] font-medium text-indigo-700">Overnight shift</span>
                            )}
                            {shift.maintenance_break_minutes > 0 && <span className="ml-2">(Break: {shift.maintenance_break_minutes} min)</span>}
                          </p>
                          <p className="text-xs text-slate-400 mt-1">{DAYS_OF_WEEK.find(d => d.value === shift.day_of_week)?.label || "All Days"}</p>
                        </div>
                        <Button variant="danger" size="sm" onClick={() => handleDeleteShift(shift.id)}>Delete</Button>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Parameter Health Configuration</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="mb-4 p-4 bg-blue-50 rounded-lg">
                  <p className="text-sm text-blue-800"><strong>Health Score:</strong> Configurable ranges and weights for each parameter. The overall health score (0-100%) is calculated when the machine is RUNNING.</p>
                  <p className="text-sm text-blue-800 mt-1"><strong>Machine State:</strong> Health scoring only activates when machine_state = RUNNING. For OFF, IDLE, UNLOAD, POWER CUT states, the score shows as "Standby".</p>
                  <p className="text-sm text-blue-800 mt-1"><strong>Weights:</strong> All active parameter weights must sum to 100%.</p>
                </div>
                
                {dynamicMetrics.length > 0 ? (
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {dynamicMetrics.map((metric) => {
                      const config = healthConfigs.find(c => c.parameter_name === metric);
                      return (
                        <div key={metric} className={`p-4 rounded-lg border ${config?.is_active ? "bg-white" : "bg-slate-50 opacity-60"}`}>
                          <div className="flex items-center justify-between mb-2">
                            <h4 className="font-medium">{METRIC_LABELS[metric] || metric}</h4>
                            {config && <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded">Configured</span>}
                          </div>
                          {config ? (
                            <div className="text-sm text-slate-600 space-y-1">
                              <p>Normal: {config.normal_min ?? "—"} - {config.normal_max ?? "—"}</p>
                              <p>Max: {config.max_min ?? "—"} - {config.max_max ?? "—"}</p>
                              <p>Weight: {config.weight}%</p>
                              <p>Ignore Zero: {config.ignore_zero_value ? "Yes" : "No"}</p>
                            </div>
                          ) : (
                            <p className="text-sm text-slate-500">Not configured</p>
                          )}
                          <Button size="sm" className="mt-3 w-full" onClick={() => { setSelectedMetric(metric); setShowHealthConfig(true); }}>
                            {config ? "Edit" : "Configure"}
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-center py-8 text-slate-500">No telemetry parameters available</div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Idle Running Configuration</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <p className="text-sm text-slate-600">
                    If current is below this value (but not zero), the machine is considered idle.
                  </p>
                  <div className="flex flex-col md:flex-row md:items-end gap-3">
                    <div className="w-full md:w-72">
                      <label className="block text-sm font-medium mb-1">Idle Current Threshold (A)</label>
                      <input
                        type="number"
                        min="0.01"
                        step="0.01"
                        value={idleThresholdInput}
                        onChange={(e) => setIdleThresholdInput(e.target.value)}
                        className="w-full px-3 py-2 border rounded-md"
                        placeholder="e.g. 5.00"
                      />
                    </div>
                    <Button onClick={handleSaveIdleThreshold} disabled={idleSaving}>
                      {idleSaving ? "Saving..." : "Save Threshold"}
                    </Button>
                  </div>
                  {idleSaveMessage && (
                    <p className="text-sm text-emerald-700">{idleSaveMessage}</p>
                  )}
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 space-y-1">
                    <p>
                      Auto-detected current field:{" "}
                      <span className="font-semibold text-slate-800">
                        {currentState?.current_field || "Not detected"}
                      </span>
                    </p>
                    <p>
                      Device type:{" "}
                      <span className="font-semibold text-slate-800 capitalize">
                        {machine.data_source_type || "metered"}
                      </span>
                    </p>
                    {!currentState?.current_field && (
                      <p className="text-amber-700">
                        No current parameter found in telemetry. Idle detection will remain unavailable until current data is received.
                      </p>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {activeTab === "rules" && <MachineRulesView deviceId={deviceId} />}
      </div>
      
      <HealthConfigModal
        isOpen={showHealthConfig}
        onClose={() => { setShowHealthConfig(false); setSelectedMetric(""); }}
        deviceId={deviceId}
        metric={selectedMetric}
        existingConfig={healthConfigs.find(c => c.parameter_name === selectedMetric) || null}
        allConfigs={healthConfigs}
        onSave={handleSaveHealthConfig}
        onDelete={handleDeleteHealthConfig}
      />
    </div>
  );
}
