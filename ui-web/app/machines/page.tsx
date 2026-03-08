"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { getCurrentState, DeviceLoadState, getDashboardSummary, DashboardSummaryData } from "@/lib/deviceApi";
import {
  ActivityEvent,
  getActivityEvents,
  getActivityUnreadCount,
  markAllActivityRead,
  clearActivityHistory,
} from "@/lib/dataApi";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatIST } from "@/lib/utils";

type MachineCard = {
  id: string;
  name: string;
  type: string;
  runtime_status: string;
  location: string;
  last_seen_timestamp: string | null;
  health_score: number | null;
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  rule_created: "Rule Created",
  rule_updated: "Rule Updated",
  rule_deleted: "Rule Deleted",
  rule_archived: "Rule Archived",
  rule_triggered: "Rule Triggered",
  alert_acknowledged: "Alert Acknowledged",
  alert_resolved: "Alert Resolved",
  alert_cleared: "Alert Cleared",
};

export default function MachinesPage() {
  const [dashboard, setDashboard] = useState<DashboardSummaryData | null>(null);
  const [machines, setMachines] = useState<MachineCard[]>([]);
  const [loadStates, setLoadStates] = useState<Record<string, DeviceLoadState>>({});
  const [globalAlerts, setGlobalAlerts] = useState<ActivityEvent[]>([]);
  const [globalUnreadCount, setGlobalUnreadCount] = useState(0);
  const [showGlobalAlerts, setShowGlobalAlerts] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboard = useCallback(async () => {
    setError(null);
    try {
      const data = await getDashboardSummary();
      const normalizedMachines: MachineCard[] = (data.devices || []).map((d) => ({
        id: d.device_id,
        name: d.device_name,
        type: d.device_type,
        runtime_status: d.runtime_status || "stopped",
        location: d.location || "",
        last_seen_timestamp: d.last_seen_timestamp,
        health_score: d.health_score,
      }));

      setDashboard(data);
      setMachines(normalizedMachines);

      const states = await Promise.all(
        normalizedMachines.map(async (d) => {
          try {
            const s = await getCurrentState(d.id);
            return [d.id, s.state] as const;
          } catch {
            return [d.id, "unknown"] as const;
          }
        })
      );
      setLoadStates(Object.fromEntries(states));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch machines");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchGlobalAlerts = useCallback(async () => {
    try {
      const [eventsResult, unreadCount] = await Promise.all([
        getActivityEvents({ page: 1, pageSize: 25 }),
        getActivityUnreadCount(),
      ]);
      setGlobalAlerts(eventsResult.data);
      setGlobalUnreadCount(unreadCount);
    } catch {
      // Keep the dashboard usable even if alert feed is temporarily unavailable.
      setGlobalAlerts([]);
      setGlobalUnreadCount(0);
    }
  }, []);

  const configuredHealthCount = useMemo(
    () => machines.filter((m) => m.health_score !== null && m.health_score !== undefined).length,
    [machines]
  );
  const notConfiguredHealthCount = useMemo(
    () => machines.filter((m) => m.health_score === null || m.health_score === undefined).length,
    [machines]
  );

  const loadBadge = (state: DeviceLoadState) => {
    const cfg: Record<DeviceLoadState, { label: string; className: string }> = {
      running: { label: "In Load", className: "bg-emerald-100 text-emerald-800 border-emerald-200" },
      idle: { label: "Idle", className: "bg-amber-100 text-amber-800 border-amber-200" },
      unloaded: { label: "Unloaded", className: "bg-orange-100 text-orange-800 border-orange-200" },
      unknown: { label: "Unknown", className: "bg-slate-100 text-slate-700 border-slate-200" },
    };
    const item = cfg[state] || cfg.unknown;
    return (
      <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${item.className}`}>
        {item.label}
      </span>
    );
  };

  const getEffectiveLoadState = (
    runtimeStatus: string | undefined,
    rawState: DeviceLoadState | undefined
  ): DeviceLoadState => {
    if ((runtimeStatus || "").toLowerCase() !== "running") {
      return "unknown";
    }
    return rawState || "unknown";
  };

  const getHealthTone = (score: number | null) => {
    if (score === null || score === undefined) {
      return {
        label: "Not configured",
        valueClass: "text-slate-500",
        barClass: "bg-slate-300",
      };
    }
    if (score >= 75) {
      return {
        label: "Healthy",
        valueClass: "text-emerald-600",
        barClass: "bg-emerald-500",
      };
    }
    if (score >= 50) {
      return {
        label: "Moderate",
        valueClass: "text-amber-600",
        barClass: "bg-amber-500",
      };
    }
    return {
      label: "Attention",
      valueClass: "text-rose-600",
      barClass: "bg-rose-500",
    };
  };

  const formatEventType = (eventType: string) => EVENT_TYPE_LABELS[eventType] || eventType.replace(/_/g, " ");

  const handleMarkAllRead = async () => {
    try {
      await markAllActivityRead();
      await fetchGlobalAlerts();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to mark alerts as read");
    }
  };

  const handleClearHistory = async () => {
    if (!confirm("Clear all global alert history?")) return;
    try {
      await clearActivityHistory();
      await fetchGlobalAlerts();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear alert history");
    }
  };

  useEffect(() => {
    fetchDashboard();
    fetchGlobalAlerts();

    const dashboardInterval = setInterval(fetchDashboard, 10000);
    const alertsInterval = setInterval(fetchGlobalAlerts, 5000);

    return () => {
      clearInterval(dashboardInterval);
      clearInterval(alertsInterval);
    };
  }, [fetchDashboard, fetchGlobalAlerts]);

  if (loading) {
    return (
      <div className="p-8">
        <div className="flex items-center justify-center h-64">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
            <p className="mt-4 text-slate-600">Loading machines...</p>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6">
          <h2 className="text-red-800 font-semibold mb-2">Error loading machines</h2>
          <p className="text-red-600">{error}</p>
          <Button
            variant="outline"
            className="mt-4"
            onClick={() => window.location.reload()}
          >
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-8 relative">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Machines</h1>
            <p className="text-slate-500 mt-1">
              Operational dashboard across all devices
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setShowGlobalAlerts((prev) => !prev)}
              className="relative inline-flex items-center justify-center w-11 h-11 rounded-xl border border-slate-200 bg-white hover:bg-slate-50"
              title="Global alert history"
            >
              <svg className="w-5 h-5 text-slate-700" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2a2 2 0 0 1-.6 1.4L4 17h5" />
                <path d="M10 17a2 2 0 0 0 4 0" />
              </svg>
              {globalUnreadCount > 0 && (
                <span className="absolute -top-1 -right-1 min-w-5 h-5 px-1 rounded-full bg-red-600 text-white text-[10px] leading-5 text-center">
                  {globalUnreadCount > 99 ? "99+" : globalUnreadCount}
                </span>
              )}
            </button>
            <div className="text-sm text-slate-500">
              {machines.length} device{machines.length !== 1 ? "s" : ""}
            </div>
          </div>

          {showGlobalAlerts && (
            <div className="absolute right-0 top-14 z-40 w-[460px] max-h-[520px] bg-white border border-slate-200 shadow-xl rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-slate-900">Global Alerts</p>
                  <p className="text-xs text-slate-500">All devices</p>
                </div>
                <button
                  type="button"
                  onClick={() => setShowGlobalAlerts(false)}
                  className="text-slate-400 hover:text-slate-700"
                >
                  ✕
                </button>
              </div>
              <div className="max-h-[380px] overflow-y-auto p-3 space-y-3">
                {globalAlerts.length === 0 ? (
                  <div className="text-center text-sm text-slate-500 py-8">No alert history</div>
                ) : (
                  globalAlerts.map((event) => (
                    <div
                      key={event.eventId}
                      className={`rounded-lg border p-3 ${
                        event.isRead ? "bg-slate-50 border-slate-200" : "bg-red-50 border-red-200"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-semibold text-slate-900">{event.title}</p>
                        <span className="text-[11px] px-2 py-0.5 rounded bg-slate-100 text-slate-700">
                          {formatEventType(event.eventType)}
                        </span>
                      </div>
                      <p className="text-xs text-slate-600 mt-1">{event.message}</p>
                      <div className="mt-2 text-[11px] text-slate-500 flex items-center justify-between">
                        <span>{event.deviceId || "all-devices"}</span>
                        <span>{formatIST(event.createdAt, "No timestamp")}</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
              <div className="px-4 py-3 border-t border-slate-200 flex items-center justify-between gap-2">
                <Button variant="outline" size="sm" onClick={handleMarkAllRead}>
                  Mark all read
                </Button>
                <Button variant="danger" size="sm" onClick={handleClearHistory}>
                  Clear history
                </Button>
              </div>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-8">
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Total Devices</p>
            <p className="text-4xl font-bold text-slate-900 mt-2">{dashboard?.summary.total_devices ?? machines.length}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Active Alerts</p>
            <p className="text-4xl font-bold text-rose-600 mt-2">{dashboard?.alerts.active_alerts ?? 0}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">System Health</p>
            <p className="text-4xl font-bold text-slate-900 mt-2">
              {dashboard?.summary.system_health !== null && dashboard?.summary.system_health !== undefined
                ? `${dashboard.summary.system_health.toFixed(1)}%`
                : "—"}
            </p>
            <p className="text-xs text-slate-500 mt-2">
              Configured: {configuredHealthCount} / {machines.length} • Not configured: {notConfiguredHealthCount}
            </p>
          </div>
        </div>

        {machines.length === 0 ? (
          <div className="bg-white rounded-lg border border-slate-200 p-12 text-center">
            <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg
                className="w-8 h-8 text-slate-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"
                />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-slate-900 mb-2">No machines found</h3>
            <p className="text-slate-500 mb-4">
              Get started by adding your first machine to the platform.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {machines.map((machine) => (
              <Link key={machine.id} href={`/machines/${machine.id}`}>
                <Card className="hover:shadow-lg transition-shadow cursor-pointer h-full">
                  <CardHeader className="pb-3">
                    <div className="flex items-start justify-between">
                      <div>
                        <h2 className="text-lg font-semibold text-slate-900">
                          {machine.name}
                        </h2>
                        <p className="text-sm text-slate-500 font-mono mt-0.5">
                          {machine.id}
                        </p>
                        <div className="mt-2">
                          {loadBadge(getEffectiveLoadState(machine.runtime_status, loadStates[machine.id]))}
                        </div>
                      </div>
                      <StatusBadge status={machine.runtime_status} />
                    </div>
                  </CardHeader>
                  <CardContent className="pt-0">
                    <div className="space-y-3">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-slate-500">Type</span>
                        <span className="text-slate-900 capitalize">
                          {machine.type}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-slate-500">Location</span>
                        <span className="text-slate-900">
                          {machine.location || "—"}
                        </span>
                      </div>
                      {machine.last_seen_timestamp ? (
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-slate-500">Last Seen</span>
                          <span className="text-slate-900 text-xs">
                            {formatIST(machine.last_seen_timestamp)}
                          </span>
                        </div>
                      ) : (
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-slate-500">Last Seen</span>
                          <span className="text-slate-900 text-xs">No data received</span>
                        </div>
                      )}

                      <div className="pt-2">
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-slate-500">Health Score</span>
                          {machine.health_score !== null && machine.health_score !== undefined ? (
                            <span className={`font-semibold ${getHealthTone(machine.health_score).valueClass}`}>
                              {machine.health_score.toFixed(1)}%
                            </span>
                          ) : (
                            <span className="text-slate-500 font-medium">Not configured</span>
                          )}
                        </div>
                        <div className="mt-2 h-2 rounded-full bg-slate-200 overflow-hidden">
                          <div
                            className={`h-full ${getHealthTone(machine.health_score).barClass}`}
                            style={{
                              width:
                                machine.health_score !== null && machine.health_score !== undefined
                                  ? `${Math.max(0, Math.min(100, machine.health_score))}%`
                                  : "0%",
                            }}
                          />
                        </div>
                      </div>
                    </div>
                    <div className="mt-4 pt-4 border-t border-slate-100">
                      <span className="text-sm text-blue-600 font-medium flex items-center gap-1">
                        View Dashboard
                        <svg
                          className="w-4 h-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M9 5l7 7-7 7"
                          />
                        </svg>
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
