from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class TariffSnapshot:
    rate: Optional[float]
    currency: str
    configured: bool
    stale: bool = False


class TariffCache:
    def __init__(self):
        self._snapshot: Optional[TariffSnapshot] = None
        self._expires_at: float = 0.0

    async def get(self) -> TariffSnapshot:
        now = time.time()
        if self._snapshot and now < self._expires_at:
            return self._snapshot

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{settings.REPORTING_SERVICE_URL}/api/v1/settings/tariff")
                if resp.status_code != 200:
                    raise RuntimeError(f"tariff status={resp.status_code}")
                payload = resp.json()
                rate = payload.get("rate")
                currency = (payload.get("currency") or "INR").upper()
                configured = rate is not None
                snapshot = TariffSnapshot(
                    rate=float(rate) if rate is not None else None,
                    currency=currency,
                    configured=configured,
                )
                self._snapshot = snapshot
                self._expires_at = now + max(1, settings.TARIFF_CACHE_TTL_SECONDS)
                return snapshot
        except Exception as exc:  # pragma: no cover
            logger.warning("tariff_fetch_failed", error=str(exc))
            if self._snapshot:
                return TariffSnapshot(
                    rate=self._snapshot.rate,
                    currency=self._snapshot.currency,
                    configured=self._snapshot.configured,
                    stale=True,
                )
            return TariffSnapshot(rate=None, currency="INR", configured=False, stale=True)


class DeviceClient:
    async def list_devices(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices")
            if resp.status_code != 200:
                return []
            payload = resp.json()
            return payload if isinstance(payload, list) else payload.get("data", [])

    async def get_device(self, device_id: str) -> Optional[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}")
            if resp.status_code != 200:
                return None
            payload = resp.json()
            if isinstance(payload, dict):
                return payload.get("data", payload)
            return None

    async def get_shift_config(self, device_id: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/shifts")
            if resp.status_code != 200:
                return []
            payload = resp.json()
            return payload.get("data", []) if isinstance(payload, dict) else []

    async def get_idle_config(self, device_id: str) -> Optional[float]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/idle-config")
            if resp.status_code != 200:
                return None
            payload = resp.json()
            cfg = payload.get("data", payload) if isinstance(payload, dict) else {}
            threshold = cfg.get("idle_current_threshold")
            return float(threshold) if threshold is not None else None

    async def get_waste_config(self, device_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/waste-config")
            if resp.status_code != 200:
                return {}
            payload = resp.json()
            return payload.get("data", payload) if isinstance(payload, dict) else {}

    async def get_site_waste_config(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/settings/waste-config")
            if resp.status_code != 200:
                return {}
            payload = resp.json()
            return payload.get("data", payload) if isinstance(payload, dict) else {}


tariff_cache = TariffCache()
device_client = DeviceClient()
