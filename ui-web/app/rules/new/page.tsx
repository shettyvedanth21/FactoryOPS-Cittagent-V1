"use client";

import { useState, Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";

import { createRule, updateRuleStatus } from "@/lib/ruleApi";
import { getDeviceFields } from "@/lib/dataApi";
import { ApiError } from "@/components/ApiError";

const conditions = [
  { value: ">", label: "Greater than (>)" },
  { value: "<", label: "Less than (<)" },
  { value: "=", label: "Equal to (=)" },
  { value: "!=", label: "Not equal (!=)" },
  { value: ">=", label: "Greater or equal (>=)" },
  { value: "<=", label: "Less or equal (<=)" },
];

const notificationOptions = [
  { value: "email", label: "Email" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "telegram", label: "Telegram" },
];

const ruleTypeOptions = [
  { value: "threshold", label: "Threshold Rule" },
  { value: "time_based", label: "Time-Based Rule" },
];

const cooldownOptions = [
  { value: "5", label: "5 minutes" },
  { value: "15", label: "15 minutes" },
  { value: "30", label: "30 minutes" },
  { value: "60", label: "1 hour" },
  { value: "120", label: "2 hours" },
  { value: "240", label: "4 hours" },
  { value: "1440", label: "24 hours" },
  { value: "no_repeat", label: "No repeat" },
];

const METRIC_LABELS: Record<string, string> = {
  power: "Power", voltage: "Voltage", current: "Current", temperature: "Temperature",
  pressure: "Pressure", humidity: "Humidity", vibration: "Vibration", frequency: "Frequency",
  power_factor: "Power Factor", speed: "Speed", torque: "Torque", oil_pressure: "Oil Pressure",
};

function formatFieldLabel(field: unknown): string {
  if (typeof field !== "string") return "Unknown";
  const normalized = field.trim();
  if (!normalized) return "Unknown";
  if (METRIC_LABELS[normalized]) return METRIC_LABELS[normalized];
  return normalized
    .replace(/_/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function CreateRuleContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const deviceIdFromUrl = searchParams.get("device_id") ?? "D1";

  const [ruleName, setRuleName] = useState("");
  const [ruleType, setRuleType] = useState<"threshold" | "time_based">("threshold");
  const [property, setProperty] = useState("");
  const [condition, setCondition] = useState(">");
  const [threshold, setThreshold] = useState("");
  const [timeWindowStart, setTimeWindowStart] = useState("20:00");
  const [timeWindowEnd, setTimeWindowEnd] = useState("06:00");
  const [cooldown, setCooldown] = useState("15");
  const [notificationChannels, setNotificationChannels] = useState<string[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [availableProperties, setAvailableProperties] = useState<{value: string, label: string}[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [propertiesLoading, setPropertiesLoading] = useState(true);

  // Fetch available properties from device telemetry
  useEffect(() => {
    async function fetchProperties() {
      try {
        const fields = await getDeviceFields(deviceIdFromUrl);
        const properties = fields
          .filter((field): field is string => typeof field === "string" && field.trim().length > 0)
          .map(field => ({
          value: field,
          label: formatFieldLabel(field)
        }));
        setAvailableProperties(properties);
        if (fields.length > 0 && !property) {
          setProperty(fields[0]);
        }
      } catch (err) {
        console.error("Failed to fetch device fields:", err);
        setAvailableProperties([]);
      } finally {
        setPropertiesLoading(false);
      }
    }
    
    fetchProperties();
  }, [deviceIdFromUrl]);

  // Update property when device changes
  useEffect(() => {
    if (availableProperties.length > 0 && !availableProperties.find(p => p.value === property)) {
      setProperty(availableProperties[0].value);
    }
  }, [availableProperties, property]);

  const handleNotificationChange = (channel: string) => {
    setNotificationChannels((prev) =>
      prev.includes(channel)
        ? prev.filter((c) => c !== channel)
        : [...prev, channel]
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!ruleName.trim()) {
      setError("Rule name is required");
      return;
    }

    if (ruleType === "threshold" && (threshold === "" || isNaN(Number(threshold)))) {
      setError("Threshold must be a valid number");
      return;
    }

    if (notificationChannels.length === 0) {
      setError("At least one notification channel is required");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const created = await createRule({
        ruleName: ruleName.trim(),
        ruleType,
        scope: "selected_devices",
        property: ruleType === "threshold" ? property : undefined,
        condition: ruleType === "threshold" ? condition : undefined,
        threshold: ruleType === "threshold" ? Number(threshold) : undefined,
        timeWindowStart: ruleType === "time_based" ? timeWindowStart : undefined,
        timeWindowEnd: ruleType === "time_based" ? timeWindowEnd : undefined,
        timezone: "Asia/Kolkata",
        timeCondition: ruleType === "time_based" ? "running_in_window" : undefined,
        notificationChannels,
        cooldownMode: cooldown === "no_repeat" ? "no_repeat" : "interval",
        cooldownMinutes: cooldown === "no_repeat" ? 0 : Number(cooldown),
        deviceIds: [deviceIdFromUrl],
      });

      // 🔴 important fix:
      // backend always creates rule as ACTIVE
      // if user disabled it → immediately pause it
      if (!enabled && created?.rule_id) {
        await updateRuleStatus(created.rule_id, "paused");
      }

      router.push("/rules");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create rule");
    } finally {
      setLoading(false);
    }
  };

  if (error && !loading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            Create Rule
          </h2>
        </div>
        <ApiError message={error} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
          Create Rule
        </h2>
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg shadow overflow-hidden">
        <form onSubmit={handleSubmit} className="p-6 space-y-6">
          <div className="space-y-2">
            <label
              htmlFor="ruleName"
              className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Rule Name
            </label>
            <input
              type="text"
              id="ruleName"
              value={ruleName}
              onChange={(e) => setRuleName(e.target.value)}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                         bg-white dark:bg-zinc-900
                         px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Enter rule name"
              disabled={loading}
            />
          </div>

          <div className="space-y-2">
            <label
              htmlFor="ruleType"
              className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Rule Type
            </label>
            <select
              id="ruleType"
              value={ruleType}
              onChange={(e) => setRuleType(e.target.value as "threshold" | "time_based")}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                         bg-white dark:bg-zinc-900
                         px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
              disabled={loading}
            >
              {ruleTypeOptions.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            <label
              htmlFor="property"
              className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Property
            </label>
            {ruleType === "time_based" ? (
              <input
                value="Power Status (running)"
                disabled
                className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                         bg-zinc-50 dark:bg-zinc-800
                         px-3 py-2 text-sm text-zinc-700 dark:text-zinc-300"
              />
            ) : propertiesLoading ? (
              <div className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-3 py-2 text-sm text-zinc-500">
                Loading properties...
              </div>
            ) : availableProperties.length === 0 ? (
              <div className="w-full rounded-md border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 px-3 py-2 text-sm text-red-600">
                No numeric properties found for this device
              </div>
            ) : (
              <select
                id="property"
                value={property}
                onChange={(e) => setProperty(e.target.value)}
                className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                           bg-white dark:bg-zinc-900
                           px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                           focus:outline-none focus:ring-2 focus:ring-blue-500"
                disabled={loading}
              >
                {availableProperties.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            )}
            <p className="text-xs text-zinc-500">
              Properties are fetched dynamically from device telemetry
            </p>
          </div>

          {ruleType === "threshold" ? (
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label
                  htmlFor="condition"
                  className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Condition
                </label>
                <select
                  id="condition"
                  value={condition}
                  onChange={(e) => setCondition(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                           bg-white dark:bg-zinc-900
                           px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                           focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={loading}
                >
                  {conditions.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <label
                  htmlFor="threshold"
                  className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Threshold
                </label>
                <input
                  type="number"
                  id="threshold"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                           bg-white dark:bg-zinc-900
                           px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                           focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Enter threshold"
                  disabled={loading}
                  step="any"
                />
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">Restricted From (IST)</label>
                <input
                  type="time"
                  value={timeWindowStart}
                  onChange={(e) => setTimeWindowStart(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={loading}
                />
              </div>
              <div className="space-y-2">
                <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">Restricted To (IST)</label>
                <input
                  type="time"
                  value={timeWindowEnd}
                  onChange={(e) => setTimeWindowEnd(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={loading}
                />
              </div>
            </div>
          )}

          <div className="space-y-2">
            <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">Cooldown</label>
            <select
              value={cooldown}
              onChange={(e) => setCooldown(e.target.value)}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
              disabled={loading}
            >
              {cooldownOptions.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            <span className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Notification Channels
            </span>

            <div className="space-y-2">
              {notificationOptions.map((option) => (
                <label
                  key={option.value}
                  className="flex items-center space-x-3 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={notificationChannels.includes(option.value)}
                    onChange={() =>
                      handleNotificationChange(option.value)
                    }
                    className="rounded border-zinc-300 dark:border-zinc-700
                               text-blue-600 focus:ring-blue-500"
                    disabled={loading}
                  />
                  <span className="text-sm text-zinc-700 dark:text-zinc-300">
                    {option.label}
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <input
              type="checkbox"
              id="enabled"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="rounded border-zinc-300 dark:border-zinc-700
                         text-blue-600 focus:ring-blue-500"
              disabled={loading}
            />
            <label
              htmlFor="enabled"
              className="text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Enable rule
            </label>
          </div>

          <div className="flex items-center gap-3 pt-4 border-t border-zinc-200 dark:border-zinc-700">
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium
                         hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {loading ? "Creating..." : "Create Rule"}
            </button>

            <button
              type="button"
              onClick={() => router.push("/rules")}
              disabled={loading}
              className="px-4 py-2 border border-zinc-300 dark:border-zinc-700
                         text-zinc-700 dark:text-zinc-300 rounded-md text-sm font-medium
                         hover:bg-zinc-50 dark:hover:bg-zinc-800"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function CreateRulePage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <CreateRuleContent />
    </Suspense>
  );
}
