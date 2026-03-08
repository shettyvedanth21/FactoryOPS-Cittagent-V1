import { RULE_ENGINE_SERVICE_BASE } from "./api";

/* ---------- types ---------- */

export type RuleStatus = "active" | "paused" | "archived";
export type RuleScope = "all_devices" | "selected_devices";
export type RuleType = "threshold" | "time_based";
export type CooldownMode = "interval" | "no_repeat";

export interface Rule {
  ruleId: string;
  ruleName: string;
  description?: string | null;
  ruleType: RuleType;
  scope: RuleScope;
  property?: string | null;
  condition?: string | null;
  threshold?: number | null;
  timeWindowStart?: string | null;
  timeWindowEnd?: string | null;
  timezone?: string | null;
  timeCondition?: string | null;
  notificationChannels: string[];
  cooldownMinutes: number;
  cooldownMode: CooldownMode;
  triggeredOnce: boolean;
  deviceIds: string[];
  status: RuleStatus;
  createdAt: string;
  updatedAt?: string | null;
  lastTriggeredAt?: string | null;
}

interface RawRule {
  rule_id: string;
  rule_name: string;
  description?: string | null;
  rule_type?: RuleType;
  scope: RuleScope;
  property?: string | null;
  condition?: string | null;
  threshold?: number | null;
  time_window_start?: string | null;
  time_window_end?: string | null;
  timezone?: string | null;
  time_condition?: string | null;
  notification_channels: string[];
  cooldown_minutes: number;
  cooldown_mode?: CooldownMode;
  triggered_once?: boolean;
  device_ids: string[];
  status: RuleStatus;
  created_at: string;
  updated_at?: string | null;
  last_triggered_at?: string | null;
}

/* ---------- list ---------- */

export async function listRules(params?: {
  deviceId?: string;
  status?: RuleStatus;
  page?: number;
  pageSize?: number;
}) {
  const query = new URLSearchParams();

  if (params?.deviceId) query.append("device_id", params.deviceId);
  if (params?.status) query.append("status", params.status);

  query.append("page", String(params?.page ?? 1));
  query.append("page_size", String(params?.pageSize ?? 20));

  const res = await fetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/rules?${query.toString()}`
  );

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  const json = await res.json();
  const rows: RawRule[] = Array.isArray(json.data) ? json.data : [];

  return {
    data: rows.map((r) => ({
      ruleId: r.rule_id,
      ruleName: r.rule_name,
      description: r.description,
      ruleType: r.rule_type ?? "threshold",
      scope: r.scope,
      property: r.property,
      condition: r.condition,
      threshold: r.threshold,
      timeWindowStart: r.time_window_start,
      timeWindowEnd: r.time_window_end,
      timezone: r.timezone ?? "Asia/Kolkata",
      timeCondition: r.time_condition,
      notificationChannels: r.notification_channels,
      cooldownMinutes: r.cooldown_minutes,
      cooldownMode: r.cooldown_mode ?? "interval",
      triggeredOnce: Boolean(r.triggered_once),
      deviceIds: r.device_ids,
      status: r.status,
      createdAt: r.created_at,
      updatedAt: r.updated_at ?? null,
      lastTriggeredAt: r.last_triggered_at ?? null,
    })),
    total: json.total,
  };
}

export async function getRule(ruleId: string): Promise<Rule> {
  const res = await fetch(`${RULE_ENGINE_SERVICE_BASE}/api/v1/rules/${ruleId}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  const json = await res.json();
  const r: RawRule = json.data;

  return {
    ruleId: r.rule_id,
    ruleName: r.rule_name,
    description: r.description,
    ruleType: r.rule_type ?? "threshold",
    scope: r.scope,
    property: r.property,
    condition: r.condition,
    threshold: r.threshold,
    timeWindowStart: r.time_window_start,
    timeWindowEnd: r.time_window_end,
    timezone: r.timezone ?? "Asia/Kolkata",
    timeCondition: r.time_condition,
    notificationChannels: r.notification_channels,
    cooldownMinutes: r.cooldown_minutes,
    cooldownMode: r.cooldown_mode ?? "interval",
    triggeredOnce: Boolean(r.triggered_once),
    deviceIds: r.device_ids || [],
    status: r.status,
    createdAt: r.created_at,
    updatedAt: r.updated_at ?? null,
    lastTriggeredAt: r.last_triggered_at ?? null,
  };
}

/* ---------- create ---------- */

export async function createRule(payload: {
  ruleName: string;
  description?: string;
  ruleType?: RuleType;
  scope: RuleScope;
  property?: string;
  condition?: string;
  threshold?: number;
  timeWindowStart?: string;
  timeWindowEnd?: string;
  timezone?: string;
  timeCondition?: string;
  notificationChannels: string[];
  cooldownMinutes?: number;
  cooldownMode?: CooldownMode;
  deviceIds: string[];
}) {
  const res = await fetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/rules`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rule_name: payload.ruleName,
        description: payload.description,
        rule_type: payload.ruleType ?? "threshold",
        scope: payload.scope,
        property: payload.property,
        condition: payload.condition,
        threshold: payload.threshold,
        time_window_start: payload.timeWindowStart,
        time_window_end: payload.timeWindowEnd,
        timezone: payload.timezone ?? "Asia/Kolkata",
        time_condition: payload.timeCondition,
        notification_channels: payload.notificationChannels,
        cooldown_minutes: payload.cooldownMinutes ?? 15,
        cooldown_mode: payload.cooldownMode ?? "interval",
        device_ids: payload.deviceIds,
      }),
    }
  );

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  const json = await res.json();
  return json.data;
}

/* ---------- pause / resume ---------- */

export async function updateRuleStatus(
  ruleId: string,
  status: RuleStatus
) {
  const res = await fetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/rules/${ruleId}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }
  );

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  return res.json();
}

/* ---------- delete ---------- */

export async function deleteRule(ruleId: string) {
  const res = await fetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/rules/${ruleId}`,
    { method: "DELETE" }
  );

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  return res.json();
}
