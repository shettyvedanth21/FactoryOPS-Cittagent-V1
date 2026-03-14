"""Idle running detection and cost aggregation service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
import logging

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.device import Device, IdleRunningLog, WasteSiteConfig, TELEMETRY_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class TariffCache:
    """In-memory tariff cache with 60s TTL to reduce cross-service calls."""

    _value: Optional[dict[str, Any]] = None
    _expires_at: Optional[datetime] = None
    _ttl_seconds: int = 60

    @classmethod
    async def get(cls) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if cls._value and cls._expires_at and now < cls._expires_at:
            return {**cls._value, "cache": "hit"}

        url = f"{settings.REPORTING_SERVICE_BASE_URL}/api/v1/settings/tariff"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw = resp.json()
                data = raw.get("data", raw) if isinstance(raw, dict) else {}
                rate = data.get("rate")
                currency = data.get("currency", "INR")
                configured = rate is not None
                cls._value = {
                    "configured": configured,
                    "rate": float(rate) if configured else None,
                    "currency": currency,
                    "updated_at": data.get("updated_at"),
                    "stale": False,
                }
                cls._expires_at = now + timedelta(seconds=cls._ttl_seconds)
                return {**cls._value, "cache": "miss"}
        except Exception as exc:
            logger.warning("tariff_fetch_failed", extra={"error": str(exc)})
            if cls._value:
                stale = {**cls._value, "stale": True, "cache": "stale"}
                return stale
            return {
                "configured": False,
                "rate": None,
                "currency": "INR",
                "updated_at": None,
                "stale": True,
                "cache": "empty",
            }


@dataclass
class MappedTelemetry:
    current: Optional[float]
    voltage: Optional[float]
    power: Optional[float]
    power_factor: Optional[float]
    current_field: Optional[str]
    voltage_field: Optional[str]


class IdleRunningService:
    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def detect_device_state(current: Optional[float], voltage: Optional[float], threshold: Optional[float]) -> str:
        if current is None or voltage is None:
            return "unknown"
        if current <= 0 and voltage > 0:
            return "unloaded"
        if threshold is not None and current > 0 and current < threshold and voltage > 0:
            return "idle"
        if threshold is not None and current >= threshold and voltage > 0:
            return "running"
        if threshold is None and current > 0 and voltage > 0:
            return "unknown"
        return "unknown"

    @staticmethod
    def _normalized_numeric_fields(row: dict[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, v in row.items():
            if k in {"timestamp", "device_id", "schema_version", "enrichment_status", "table"}:
                continue
            if isinstance(v, (int, float)):
                out[k] = float(v)
        return out

    @staticmethod
    def _detect_current(fields: dict[str, float]) -> tuple[Optional[float], Optional[str]]:
        if "current" in fields:
            return fields["current"], "current"

        explicit_aliases = ["current_L1", "i_L1", "phase_current", "current_l1", "i_l1"]
        for alias in explicit_aliases:
            if alias in fields:
                return fields[alias], alias

        phase_keys = [k for k in fields.keys() if k.lower() in {"current_l1", "current_l2", "current_l3", "i_l1", "i_l2", "i_l3"}]
        if phase_keys:
            max_val = max(fields[k] for k in phase_keys)
            return max_val, "max(" + ",".join(sorted(phase_keys)) + ")"

        contains = [k for k in fields.keys() if "current" in k.lower()]
        if contains:
            key = sorted(contains)[0]
            return fields[key], key

        return None, None

    @staticmethod
    def _detect_voltage(fields: dict[str, float]) -> tuple[Optional[float], Optional[str]]:
        if "voltage" in fields:
            return fields["voltage"], "voltage"

        explicit_aliases = ["voltage_L1", "v_L1", "voltage_l1", "v_l1"]
        for alias in explicit_aliases:
            if alias in fields:
                return fields[alias], alias

        phase_keys = [k for k in fields.keys() if k.lower() in {"voltage_l1", "voltage_l2", "voltage_l3", "v_l1", "v_l2", "v_l3"}]
        if phase_keys:
            avg = sum(fields[k] for k in phase_keys) / len(phase_keys)
            return avg, "avg(" + ",".join(sorted(phase_keys)) + ")"

        contains = [k for k in fields.keys() if "voltage" in k.lower()]
        if contains:
            key = sorted(contains)[0]
            return fields[key], key

        return None, None

    @staticmethod
    def _detect_power(fields: dict[str, float]) -> Optional[float]:
        if "power" in fields:
            return fields["power"]
        contains = [k for k in fields.keys() if k.lower() == "power" or "active_power" in k.lower()]
        if contains:
            return fields[sorted(contains)[0]]
        return None

    @staticmethod
    def _detect_pf(fields: dict[str, float]) -> Optional[float]:
        for key in ["power_factor", "pf", "cos_phi", "powerfactor"]:
            if key in fields:
                return fields[key]
        contains = [k for k in fields.keys() if "power_factor" in k.lower() or k.lower() == "pf"]
        if contains:
            return fields[sorted(contains)[0]]
        return None

    @classmethod
    def map_telemetry(cls, row: dict[str, Any]) -> MappedTelemetry:
        fields = cls._normalized_numeric_fields(row)
        current, current_field = cls._detect_current(fields)
        voltage, voltage_field = cls._detect_voltage(fields)
        power = cls._detect_power(fields)
        power_factor = cls._detect_pf(fields)
        return MappedTelemetry(
            current=current,
            voltage=voltage,
            power=power,
            power_factor=power_factor,
            current_field=current_field,
            voltage_field=voltage_field,
        )

    async def _fetch_telemetry(
        self,
        device_id: str,
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if start_time:
            params["start_time"] = start_time.isoformat()
        if end_time:
            params["end_time"] = end_time.isoformat()
        if limit is not None:
            params["limit"] = str(limit)

        url = f"{settings.DATA_SERVICE_BASE_URL}/api/v1/data/telemetry/{device_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict):
                items = payload.get("data", {}).get("items", [])
            else:
                items = payload if isinstance(payload, list) else []

        def parse_ts(item: dict[str, Any]) -> float:
            ts = item.get("timestamp")
            if not ts:
                return 0.0
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0

        return sorted(items, key=parse_ts)

    async def _get_device(self, device_id: str) -> Optional[Device]:
        result = await self._session.execute(select(Device).where(Device.device_id == device_id, Device.deleted_at.is_(None)))
        return result.scalar_one_or_none()

    async def get_idle_config(self, device_id: str) -> dict[str, Any]:
        device = await self._get_device(device_id)
        if not device:
            raise ValueError("Device not found")
        threshold = float(device.idle_current_threshold) if device.idle_current_threshold is not None else None
        return {
            "device_id": device_id,
            "idle_current_threshold": threshold,
            "configured": threshold is not None,
        }

    async def set_idle_config(self, device_id: str, threshold: float) -> dict[str, Any]:
        device = await self._get_device(device_id)
        if not device:
            raise ValueError("Device not found")
        device.idle_current_threshold = Decimal(str(round(float(threshold), 4)))
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(device)
        return {
            "device_id": device_id,
            "idle_current_threshold": float(device.idle_current_threshold),
            "configured": True,
        }

    async def get_waste_config(self, device_id: str) -> dict[str, Any]:
        device = await self._get_device(device_id)
        if not device:
            raise ValueError("Device not found")

        return {
            "device_id": device_id,
            "overconsumption_current_threshold_a": (
                float(device.overconsumption_current_threshold_a)
                if device.overconsumption_current_threshold_a is not None
                else None
            ),
            # Deprecated; kept for backward compatibility.
            "unoccupied_weekday_start_time": None,
            "unoccupied_weekday_end_time": None,
            "unoccupied_weekend_start_time": None,
            "unoccupied_weekend_end_time": None,
            "has_device_override": False,
        }

    async def set_waste_config(
        self,
        device_id: str,
        overconsumption_current_threshold_a: Optional[float],
        unoccupied_weekday_start_time: Optional[str],
        unoccupied_weekday_end_time: Optional[str],
        unoccupied_weekend_start_time: Optional[str],
        unoccupied_weekend_end_time: Optional[str],
    ) -> dict[str, Any]:
        device = await self._get_device(device_id)
        if not device:
            raise ValueError("Device not found")

        device.overconsumption_current_threshold_a = (
            Decimal(str(round(float(overconsumption_current_threshold_a), 4)))
            if overconsumption_current_threshold_a is not None
            else None
        )
        # Deprecated fields are accepted but ignored in runtime logic.
        device.unoccupied_weekday_start_time = None
        device.unoccupied_weekday_end_time = None
        device.unoccupied_weekend_start_time = None
        device.unoccupied_weekend_end_time = None

        await self._session.flush()
        await self._session.commit()
        return await self.get_waste_config(device_id)

    async def get_site_waste_config(self, tenant_id: Optional[str] = None) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            # Deprecated and intentionally disabled by policy.
            "default_unoccupied_weekday_start_time": None,
            "default_unoccupied_weekday_end_time": None,
            "default_unoccupied_weekend_start_time": None,
            "default_unoccupied_weekend_end_time": None,
            "timezone": "Asia/Kolkata",
            "configured": False,
        }

    async def set_site_waste_config(
        self,
        default_unoccupied_weekday_start_time: str,
        default_unoccupied_weekday_end_time: str,
        default_unoccupied_weekend_start_time: str,
        default_unoccupied_weekend_end_time: str,
        timezone_name: Optional[str],
        updated_by: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        # Deprecated endpoint: accept payload for compatibility, but keep feature disabled.
        return await self.get_site_waste_config(tenant_id)

    async def get_current_state(self, device_id: str) -> dict[str, Any]:
        device = await self._get_device(device_id)
        if not device:
            raise ValueError("Device not found")

        rows = await self._fetch_telemetry(device_id, limit=1)
        if not rows:
            return {
                "device_id": device_id,
                "state": "unknown",
                "current": None,
                "voltage": None,
                "threshold": float(device.idle_current_threshold) if device.idle_current_threshold is not None else None,
                "timestamp": None,
                "current_field": None,
                "voltage_field": None,
            }

        latest = rows[-1]
        ts_raw = latest.get("timestamp")
        latest_ts: Optional[datetime] = None
        if ts_raw:
            try:
                latest_ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                latest_ts = None

        # Never report live load state from stale telemetry.
        now_utc = datetime.now(timezone.utc)
        stale = latest_ts is None or (now_utc - latest_ts).total_seconds() > TELEMETRY_TIMEOUT_SECONDS
        runtime_status = device.get_runtime_status()
        if stale or runtime_status != "running":
            threshold = float(device.idle_current_threshold) if device.idle_current_threshold is not None else None
            mapped = self.map_telemetry(latest)
            return {
                "device_id": device_id,
                "state": "unknown",
                "current": mapped.current,
                "voltage": mapped.voltage,
                "threshold": threshold,
                "timestamp": latest.get("timestamp"),
                "current_field": mapped.current_field,
                "voltage_field": mapped.voltage_field,
            }

        mapped = self.map_telemetry(latest)
        threshold = float(device.idle_current_threshold) if device.idle_current_threshold is not None else None
        state = self.detect_device_state(mapped.current, mapped.voltage, threshold)

        return {
            "device_id": device_id,
            "state": state,
            "current": mapped.current,
            "voltage": mapped.voltage,
            "threshold": threshold,
            "timestamp": latest.get("timestamp"),
            "current_field": mapped.current_field,
            "voltage_field": mapped.voltage_field,
        }

    @staticmethod
    def _power_kw(mapped: MappedTelemetry) -> tuple[Optional[float], bool]:
        if mapped.current is None or mapped.voltage is None:
            return None, False
        if mapped.power is not None:
            return float(mapped.power) / 1000.0, False
        pf = mapped.power_factor if mapped.power_factor is not None else 1.0
        pf_estimated = mapped.power_factor is None
        return (float(mapped.current) * float(mapped.voltage) * float(pf)) / 1000.0, pf_estimated

    async def _get_or_create_day_log(self, device_id: str, day_start: datetime, now_utc: datetime) -> IdleRunningLog:
        result = await self._session.execute(
            select(IdleRunningLog).where(
                IdleRunningLog.device_id == device_id,
                IdleRunningLog.period_start == day_start,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            return row

        row = IdleRunningLog(
            device_id=device_id,
            period_start=day_start,
            period_end=day_start,
            idle_duration_sec=0,
            idle_energy_kwh=0,
            idle_cost=0,
            currency="INR",
            tariff_rate_used=0,
            pf_estimated=False,
            created_at=now_utc,
            updated_at=now_utc,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def aggregate_device_idle(self, device: Device, now_utc: Optional[datetime] = None) -> None:
        if device.idle_current_threshold is None:
            return

        now_utc = now_utc or datetime.now(timezone.utc)
        now_utc = self._to_utc(now_utc)
        day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        row = await self._get_or_create_day_log(device.device_id, day_start, now_utc)

        row_period_end = self._to_utc(row.period_end) if row.period_end else None
        from_ts = row_period_end if row_period_end and row_period_end > day_start else day_start
        if from_ts >= now_utc:
            return

        points = await self._fetch_telemetry(device.device_id, start_time=from_ts, end_time=now_utc)
        if len(points) < 2:
            row.period_end = now_utc
            await self._session.flush()
            return

        threshold = float(device.idle_current_threshold)
        added_duration = 0
        added_energy = 0.0
        any_pf_estimated = bool(row.pf_estimated)

        for i in range(len(points) - 1):
            current_point = points[i]
            next_point = points[i + 1]

            try:
                ts1 = self._to_utc(datetime.fromisoformat(str(current_point.get("timestamp")).replace("Z", "+00:00")))
                ts2 = self._to_utc(datetime.fromisoformat(str(next_point.get("timestamp")).replace("Z", "+00:00")))
            except Exception:
                continue

            duration = int(max((ts2 - ts1).total_seconds(), 0))
            if duration <= 0:
                continue

            mapped = self.map_telemetry(current_point)
            state = self.detect_device_state(mapped.current, mapped.voltage, threshold)
            if state != "idle":
                continue

            power_kw, pf_estimated = self._power_kw(mapped)
            if power_kw is None or power_kw < 0:
                continue

            added_duration += duration
            added_energy += power_kw * (duration / 3600.0)
            any_pf_estimated = any_pf_estimated or pf_estimated

        row.idle_duration_sec = int(row.idle_duration_sec or 0) + added_duration
        row.idle_energy_kwh = Decimal(str(round(float(row.idle_energy_kwh or 0) + added_energy, 6)))
        row.pf_estimated = any_pf_estimated
        row.period_end = now_utc

        tariff = await TariffCache.get()
        rate = tariff.get("rate")
        row.currency = tariff.get("currency") or row.currency or "INR"
        row.tariff_rate_used = Decimal(str(rate if rate is not None else 0))
        if rate is not None:
            row.idle_cost = Decimal(str(round(float(row.idle_energy_kwh) * float(rate), 4)))

        await self._session.flush()

    async def aggregate_all_configured_devices(self) -> dict[str, int]:
        result = await self._session.execute(
            select(Device).where(
                Device.deleted_at.is_(None),
                Device.idle_current_threshold.is_not(None),
            )
        )
        devices = result.scalars().all()
        processed = 0
        failed = 0
        now_utc = datetime.now(timezone.utc)

        for device in devices:
            try:
                await self.aggregate_device_idle(device, now_utc=now_utc)
                processed += 1
            except Exception as exc:
                failed += 1
                logger.error("idle_aggregation_failed", extra={"device_id": device.device_id, "error": str(exc)})

        await self._session.commit()
        return {"processed": processed, "failed": failed, "total": len(devices)}

    @staticmethod
    def _duration_label(minutes: int) -> str:
        hours = minutes // 60
        mins = minutes % 60
        if hours > 0:
            return f"{hours} hr {mins} min"
        return f"{mins} min"

    async def get_idle_stats(self, device_id: str) -> dict[str, Any]:
        device = await self._get_device(device_id)
        if not device:
            raise ValueError("Device not found")
        data_source_type = device.data_source_type

        threshold = float(device.idle_current_threshold) if device.idle_current_threshold is not None else None
        if threshold is None:
            return {
                "device_id": device_id,
                "today": None,
                "month": None,
                "tariff_configured": False,
                "pf_estimated": False,
                "threshold_configured": False,
                "idle_current_threshold": None,
                "data_source_type": data_source_type,
            }

        # Refresh aggregate up to "now" for near-real-time widget reads.
        try:
            await self.aggregate_device_idle(device)
            await self._session.commit()
        except Exception as exc:
            logger.warning("idle_stats_refresh_failed", extra={"device_id": device_id, "error": str(exc)})

        now_utc = datetime.now(timezone.utc)
        day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        today_row = (
            await self._session.execute(
                select(IdleRunningLog).where(
                    IdleRunningLog.device_id == device_id,
                    IdleRunningLog.period_start == day_start,
                )
            )
        ).scalar_one_or_none()

        month_agg = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(IdleRunningLog.idle_duration_sec), 0),
                    func.coalesce(func.sum(IdleRunningLog.idle_energy_kwh), 0),
                    func.max(IdleRunningLog.pf_estimated),
                ).where(
                    IdleRunningLog.device_id == device_id,
                    IdleRunningLog.period_start >= month_start,
                    IdleRunningLog.period_start <= day_start,
                )
            )
        ).one()

        today_duration_sec = int(today_row.idle_duration_sec) if today_row else 0
        today_energy = float(today_row.idle_energy_kwh) if today_row else 0.0
        month_duration_sec = int(month_agg[0] or 0)
        month_energy = float(month_agg[1] or 0.0)
        pf_estimated = bool(month_agg[2] or (today_row.pf_estimated if today_row else False))

        tariff = await TariffCache.get()
        tariff_rate = tariff.get("rate")
        currency = tariff.get("currency") or "INR"

        today_cost = round(today_energy * float(tariff_rate), 2) if tariff_rate is not None else None
        month_cost = round(month_energy * float(tariff_rate), 2) if tariff_rate is not None else None

        today_minutes = today_duration_sec // 60
        month_minutes = month_duration_sec // 60

        return {
            "device_id": device_id,
            "today": {
                "idle_duration_minutes": today_minutes,
                "idle_duration_label": self._duration_label(today_minutes),
                "idle_energy_kwh": round(today_energy, 4),
                "idle_cost": today_cost,
                "currency": currency,
            },
            "month": {
                "idle_duration_minutes": month_minutes,
                "idle_duration_label": self._duration_label(month_minutes),
                "idle_energy_kwh": round(month_energy, 4),
                "idle_cost": month_cost,
                "currency": currency,
            },
            "tariff_configured": tariff_rate is not None,
            "pf_estimated": pf_estimated,
            "threshold_configured": True,
            "idle_current_threshold": threshold,
            "data_source_type": data_source_type,
            "tariff_cache": tariff.get("cache"),
            "tariff_stale": tariff.get("stale", False),
        }
    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
