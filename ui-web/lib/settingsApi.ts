import { DEVICE_SERVICE_BASE } from "./api";
const SETTINGS_BASE = "/backend/reporting/api/v1/settings";

export type CurrencyCode = "INR" | "USD" | "EUR";

export interface NotificationEmail {
  id: number;
  value: string;
  is_active: boolean;
}

export interface NotificationChannelsResponse {
  email: NotificationEmail[];
  whatsapp: NotificationEmail[];
  sms: NotificationEmail[];
}

export interface TariffConfigResponse {
  rate: number | null;
  currency: CurrencyCode;
  updated_at: string | null;
}

export interface SiteWasteConfigResponse {
  tenant_id?: string | null;
  default_unoccupied_weekday_start_time: string | null;
  default_unoccupied_weekday_end_time: string | null;
  default_unoccupied_weekend_start_time: string | null;
  default_unoccupied_weekend_end_time: string | null;
  timezone: string;
  configured: boolean;
}

export async function getNotificationChannels(): Promise<NotificationChannelsResponse> {
  const res = await fetch(`${SETTINGS_BASE}/notifications`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function addNotificationEmail(email: string): Promise<NotificationEmail> {
  const res = await fetch(`${SETTINGS_BASE}/notifications/email`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error?.message || error?.detail?.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function removeNotificationEmail(id: number): Promise<void> {
  const res = await fetch(`${SETTINGS_BASE}/notifications/email/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function getTariffConfig(): Promise<TariffConfigResponse> {
  const res = await fetch(`${SETTINGS_BASE}/tariff`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function saveTariffConfig(payload: {
  rate: number;
  currency: CurrencyCode;
  updated_by?: string;
}): Promise<TariffConfigResponse> {
  const res = await fetch(`${SETTINGS_BASE}/tariff`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error?.message || error?.detail?.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getSiteWasteConfig(): Promise<SiteWasteConfigResponse> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/settings/waste-config`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function saveSiteWasteConfig(payload: {
  default_unoccupied_weekday_start_time: string;
  default_unoccupied_weekday_end_time: string;
  default_unoccupied_weekend_start_time: string;
  default_unoccupied_weekend_end_time: string;
  timezone?: string;
  updated_by?: string;
  tenant_id?: string;
}): Promise<SiteWasteConfigResponse> {
  const res = await fetch(`${DEVICE_SERVICE_BASE}/api/v1/settings/waste-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error?.message || error?.detail?.message || `HTTP ${res.status}`);
  }
  return res.json();
}
