"""
Typed HTTP clients for live E2E tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx


TIMEOUT_SHORT = 30
TIMEOUT_LONG = 120


class DeviceClient:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_SHORT)

    def create_device(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/devices", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_device(self, device_id: str) -> dict:
        resp = self.c.get(f"/api/v1/devices/{device_id}")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def delete_device(self, device_id: str) -> None:
        self.c.delete(f"/api/v1/devices/{device_id}")

    def set_shift(self, device_id: str, payload: dict) -> dict:
        resp = self.c.post(f"/api/v1/devices/{device_id}/shifts", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_shifts(self, device_id: str) -> list:
        resp = self.c.get(f"/api/v1/devices/{device_id}/shifts")
        resp.raise_for_status()
        return self._unwrap_list(resp.json())

    def set_idle_config(self, device_id: str, idle_current_threshold: float) -> dict:
        resp = self.c.post(
            f"/api/v1/devices/{device_id}/idle-config",
            json={"idle_current_threshold": idle_current_threshold},
        )
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_idle_config(self, device_id: str) -> dict:
        resp = self.c.get(f"/api/v1/devices/{device_id}/idle-config")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def set_waste_config(self, device_id: str, overconsumption_current_threshold_a: float) -> dict:
        resp = self.c.put(
            f"/api/v1/devices/{device_id}/waste-config",
            json={"overconsumption_current_threshold_a": overconsumption_current_threshold_a},
        )
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_current_state(self, device_id: str) -> dict:
        resp = self.c.get(f"/api/v1/devices/{device_id}/current-state")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def set_parameter_health(self, device_id: str, payload: dict) -> list:
        parameters = payload.get("parameters", [])
        created = []
        for item in parameters:
            converted = {
                "parameter_name": item["field"],
                "normal_min": item.get("normal_min"),
                "normal_max": item.get("normal_max"),
                "max_min": item.get("critical_min"),
                "max_max": item.get("critical_max"),
                "weight": item.get("weight"),
                "ignore_zero_value": False,
                "is_active": True,
            }
            resp = self.c.post(f"/api/v1/devices/{device_id}/health-config", json=converted)
            resp.raise_for_status()
            created.append(self._unwrap(resp.json()))
        return created

    def calculate_health_score(self, device_id: str, telemetry_values: dict) -> dict:
        resp = self.c.post(
            f"/api/v1/devices/{device_id}/health-score",
            json={"values": telemetry_values, "machine_state": "RUNNING"},
        )
        resp.raise_for_status()
        return resp.json()

    def set_dashboard_widgets(self, device_id: str, payload: dict) -> dict:
        selected_fields = [w["field"] for w in payload.get("widgets", [])]
        resp = self.c.put(
            f"/api/v1/devices/{device_id}/dashboard-widgets",
            json={"selected_fields": selected_fields},
        )
        resp.raise_for_status()
        return resp.json()

    def _unwrap(self, body: dict) -> dict:
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def _unwrap_list(self, body) -> list:
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            for key in ("data", "items", "results", "shifts"):
                if key in body and isinstance(body[key], list):
                    return body[key]
        return []


class DataClient:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_SHORT)

    def health(self) -> dict:
        resp = self.c.get("/api/v1/data/health")
        resp.raise_for_status()
        return resp.json()

    def get_telemetry(self, device_id: str, hours_back: int = 2) -> list:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=hours_back)
        resp = self.c.get(
            f"/api/v1/data/telemetry/{device_id}",
            params={
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "limit": 2000,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict):
                return data.get("items", [])
            if isinstance(data, list):
                return data
            return body.get("items", [])
        if isinstance(body, list):
            return body
        return []

    def get_latest(self, device_id: str) -> Optional[dict]:
        items = self.get_telemetry(device_id, hours_back=1)
        return items[-1] if items else None


class RulesClient:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_SHORT)

    def create_rule(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/rules", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_rules(self) -> list:
        resp = self.c.get("/api/v1/rules")
        resp.raise_for_status()
        return self._unwrap_list(resp.json())

    def delete_rule(self, rule_id) -> None:
        self.c.delete(f"/api/v1/rules/{rule_id}")

    def get_alerts(self, device_id: Optional[str] = None) -> list:
        params = {"device_id": device_id} if device_id else {}
        resp = self.c.get("/api/v1/alerts", params=params)
        resp.raise_for_status()
        return self._unwrap_list(resp.json())

    def _unwrap(self, body):
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def _unwrap_list(self, body) -> list:
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            for key in ("data", "items", "rules", "alerts"):
                if key in body and isinstance(body[key], list):
                    return body[key]
        return []


class AnalyticsClient:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_LONG)

    def run_job(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/analytics/run", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_status(self, job_id: str) -> dict:
        resp = self.c.get(f"/api/v1/analytics/status/{job_id}")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_results(self, job_id: str) -> dict:
        resp = self.c.get(f"/api/v1/analytics/formatted-results/{job_id}")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_models(self) -> dict:
        resp = self.c.get("/api/v1/analytics/models")
        resp.raise_for_status()
        return resp.json()

    def _unwrap(self, body):
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


class ReportingClient:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_LONG)
        self.default_tenant = "default"

    def set_tariff(self, payload: dict) -> dict:
        body = {
            "rate": payload["rate_per_kwh"],
            "currency": payload.get("currency", "INR"),
        }
        resp = self.c.post("/api/v1/settings/tariff", json=body)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_tariff(self) -> dict:
        resp = self.c.get("/api/v1/settings/tariff", params={"tenant_id": self.default_tenant})
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def create_notification_channel(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/settings/notifications/email", json={"email": payload["email"]})
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_notification_channels(self) -> list:
        resp = self.c.get("/api/v1/settings/notifications")
        resp.raise_for_status()
        body = resp.json()
        return body.get("email", []) if isinstance(body, dict) else []

    def delete_notification_channel(self, channel_id) -> None:
        self.c.delete(f"/api/v1/settings/notifications/email/{channel_id}")

    def run_energy_report(self, payload: dict) -> dict:
        body = {
            "device_id": payload["device_id"],
            "start_date": payload["start_date"],
            "end_date": payload["end_date"],
            "tenant_id": self.default_tenant,
            "report_name": payload.get("report_name"),
        }
        resp = self.c.post("/api/reports/energy/consumption", json=body)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_report_status(self, report_id: str) -> dict:
        resp = self.c.get(
            f"/api/reports/{report_id}/status",
            params={"tenant_id": self.default_tenant},
        )
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def download_report(self, report_id: str) -> bytes:
        resp = self.c.get(
            f"/api/reports/{report_id}/download",
            params={"tenant_id": self.default_tenant},
        )
        resp.raise_for_status()
        return resp.content

    def _unwrap(self, body):
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


class WasteClient:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=TIMEOUT_LONG)

    def run_analysis(self, payload: dict) -> dict:
        resp = self.c.post("/api/v1/waste/analysis/run", json=payload)
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_status(self, job_id: str) -> dict:
        resp = self.c.get(f"/api/v1/waste/analysis/{job_id}/status")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def get_result(self, job_id: str) -> dict:
        resp = self.c.get(f"/api/v1/waste/analysis/{job_id}/result")
        resp.raise_for_status()
        return self._unwrap(resp.json())

    def download_pdf(self, job_id: str) -> bytes:
        resp = self.c.get(f"/api/v1/waste/analysis/{job_id}/download")
        resp.raise_for_status()
        body = resp.json()
        url = body["download_url"]
        file_resp = httpx.get(url, timeout=TIMEOUT_LONG)
        file_resp.raise_for_status()
        return file_resp.content

    def _unwrap(self, body):
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


class CopilotClient:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=60)

    def chat(self, message: str, history: Optional[list] = None) -> dict:
        resp = self.c.post(
            "/api/v1/copilot/chat",
            json={"message": message, "conversation_history": history or []},
        )
        if resp.status_code not in (200, 503):
            resp.raise_for_status()
        return resp.json()


class APIClient:
    def __init__(self, services: dict):
        self.device = DeviceClient(services["device"])
        self.data = DataClient(services["data"])
        self.rules = RulesClient(services["rules"])
        self.analytics = AnalyticsClient(services["analytics"])
        self.reporting = ReportingClient(services["reporting"])
        self.waste = WasteClient(services["waste"])
        self.copilot = CopilotClient(services["copilot"])
