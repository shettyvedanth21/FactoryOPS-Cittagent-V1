"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/badge";
import { formatIST } from "@/lib/utils";
import { getRule, updateRuleStatus, deleteRule, Rule, RuleStatus } from "@/lib/ruleApi";
import { getDevices, Device } from "@/lib/deviceApi";
import { getNotificationChannels, NotificationEmail } from "@/lib/settingsApi";

function formatScope(scope: Rule["scope"]) {
  return scope === "all_devices" ? "All Devices" : "Selected Devices";
}

function formatCooldown(rule: Rule) {
  if (rule.cooldownMode === "no_repeat") return "No repeat";
  const mins = rule.cooldownMinutes ?? 15;
  if (mins < 60) return `${mins} minutes`;
  if (mins % 60 === 0) return `${mins / 60} hour${mins / 60 > 1 ? "s" : ""}`;
  return `${mins} minutes`;
}

function formatTrigger(rule: Rule) {
  if (rule.ruleType === "time_based") {
    return `Running in restricted window ${rule.timeWindowStart ?? "--:--"} - ${rule.timeWindowEnd ?? "--:--"} IST`;
  }
  return `${rule.property ?? "property"} ${rule.condition ?? ""} ${rule.threshold ?? "-"}`.trim();
}

export default function RuleDetailsPage() {
  const params = useParams();
  const router = useRouter();
  const ruleId = (params.ruleId as string) || "";

  const [rule, setRule] = useState<Rule | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [emails, setEmails] = useState<NotificationEmail[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const emailRecipients = useMemo(() => emails.map((e) => e.value), [emails]);

  const deviceLabel = useMemo(() => {
    if (!rule) return [];
    if (rule.scope === "all_devices") return ["All Devices"];
    const map = new Map(devices.map((d) => [d.id, d.name]));
    return rule.deviceIds.map((id) => `${map.get(id) || id} (${id})`);
  }, [rule, devices]);

  const load = async () => {
    if (!ruleId) return;
    setLoading(true);
    setError(null);
    try {
      const [r, d, n] = await Promise.all([getRule(ruleId), getDevices(), getNotificationChannels()]);
      setRule(r);
      setDevices(d);
      setEmails(n.email || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load rule details");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ruleId]);

  const handleToggleStatus = async () => {
    if (!rule) return;
    const nextStatus: RuleStatus = rule.status === "active" ? "paused" : "active";
    try {
      setBusy(true);
      await updateRuleStatus(rule.ruleId, nextStatus);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update rule status");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!rule) return;
    if (!confirm("Are you sure you want to delete this rule?")) return;
    try {
      setBusy(true);
      await deleteRule(rule.ruleId);
      router.push("/rules");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete rule");
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="p-8">
        <div className="flex items-center justify-center h-64">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
            <p className="mt-4 text-slate-600">Loading rule details...</p>
          </div>
        </div>
      </div>
    );
  }

  if (error || !rule) {
    return (
      <div className="p-8">
        <div className="max-w-4xl mx-auto bg-red-50 border border-red-200 rounded-lg p-6">
          <h2 className="text-red-800 font-semibold mb-2">Unable to load rule details</h2>
          <p className="text-red-700">{error || "Rule not found"}</p>
          <div className="mt-4">
            <Link href="/rules">
              <Button variant="outline">Back to Rules</Button>
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <Link href="/rules" className="text-sm text-slate-500 hover:text-slate-800">
              ← Back to Rules
            </Link>
            <h1 className="text-3xl font-bold text-slate-900 mt-3">{rule.ruleName}</h1>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="outline" onClick={handleToggleStatus} disabled={busy}>
              {rule.status === "active" ? "Pause" : "Enable"}
            </Button>
            <Button variant="danger" onClick={handleDelete} disabled={busy}>
              Delete
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <Card>
            <CardHeader>
              <CardTitle>Rule Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-slate-500">Status</span>
                <StatusBadge status={rule.status} />
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-500">Type</span>
                <span className="text-slate-900">{rule.ruleType === "time_based" ? "Time-Based" : "Threshold"}</span>
              </div>
              <div className="flex items-start justify-between gap-3">
                <span className="text-slate-500">Trigger</span>
                <span className="text-slate-900 text-right">{formatTrigger(rule)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-500">Scope</span>
                <span className="text-slate-900">{formatScope(rule.scope)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-500">Cooldown</span>
                <span className="text-slate-900">{formatCooldown(rule)}</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Devices</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {deviceLabel.map((name) => (
                  <div key={name} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800">
                    {name}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Notification Channels</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-slate-500">Channels:</span>
              <span className="text-slate-900">{rule.notificationChannels.join(", ") || "None"}</span>
            </div>
            {rule.notificationChannels.includes("email") ? (
              <div>
                <p className="text-sm text-slate-500 mb-2">Configured email recipients ({emailRecipients.length})</p>
                {emailRecipients.length === 0 ? (
                  <p className="text-sm text-amber-700">No active emails configured in Settings.</p>
                ) : (
                  <div className="rounded-lg border border-slate-200 divide-y divide-slate-100">
                    {emailRecipients.map((email) => (
                      <div key={email} className="px-3 py-2 text-sm text-slate-800">
                        {email}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-slate-500">Email channel not enabled for this rule.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Timestamps</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-slate-500">Created</span>
              <span className="text-slate-900">{formatIST(rule.createdAt, "N/A")}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-500">Last Updated</span>
              <span className="text-slate-900">{formatIST(rule.updatedAt || null, "N/A")}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-500">Last Triggered</span>
              <span className="text-slate-900">{formatIST(rule.lastTriggeredAt || null, "Not triggered yet")}</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
