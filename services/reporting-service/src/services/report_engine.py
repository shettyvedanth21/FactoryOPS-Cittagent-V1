from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class DeviceComputationResult:
    device_id: str
    device_name: str
    data_source_type: str
    availability: dict[str, bool]
    method: str
    quality: str
    warnings: list[str]
    error: str | None
    total_kwh: float | None
    peak_demand_kw: float | None
    peak_timestamp: str | None
    average_load_kw: float | None
    load_factor_pct: float | None
    load_factor_band: str | None
    total_hours: float
    daily_breakdown: list[dict[str, Any]]
    power_factor: dict[str, Any] | None
    reactive: dict[str, Any] | None


def _to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).copy()
    if "timestamp" not in df.columns:
        return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

    numeric_candidates = [
        "energy_kwh",
        "power",
        "current",
        "voltage",
        "power_factor",
        "frequency",
        "kvar",
        "reactive_power",
        "run_hours",
        "voltage_l1",
        "voltage_l2",
        "voltage_l3",
    ]
    for col in numeric_candidates:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.reset_index(drop=True)


def _availability(df: pd.DataFrame) -> dict[str, bool]:
    fields = [
        "energy_kwh",
        "power",
        "current",
        "voltage",
        "power_factor",
        "frequency",
        "kvar",
        "reactive_power",
        "run_hours",
        "voltage_l1",
        "voltage_l2",
        "voltage_l3",
    ]
    return {f: (f in df.columns and df[f].notna().sum() > 0) for f in fields}


def _series_with_time(df: pd.DataFrame, col: str) -> tuple[np.ndarray, np.ndarray]:
    sub = df[["timestamp", col]].dropna()
    if sub.empty:
        return np.array([]), np.array([])
    ts = (sub["timestamp"].astype("int64") / 1e9).to_numpy()
    vals = sub[col].to_numpy(dtype=float)
    return ts, vals


def _integrate_kwh(ts_sec: np.ndarray, power_kw: np.ndarray) -> tuple[float | None, float]:
    if len(ts_sec) < 2 or len(power_kw) < 2:
        return None, 0.0
    ts_hours = (ts_sec - ts_sec[0]) / 3600.0
    integrate = getattr(np, "trapezoid", None)
    if integrate is None:
        integrate = getattr(np, "trapz", None)
    if integrate is None:
        raise RuntimeError("No compatible numpy integration function available")
    total_kwh = float(integrate(power_kw, ts_hours))
    total_hours = float((ts_sec[-1] - ts_sec[0]) / 3600.0)
    return max(total_kwh, 0.0), max(total_hours, 0.0)


def _load_factor_band(load_factor_pct: float | None) -> str | None:
    if load_factor_pct is None:
        return None
    if load_factor_pct < 30:
        return "poor"
    if load_factor_pct <= 70:
        return "moderate"
    return "good"


def _compute_from_df(
    df: pd.DataFrame,
    device_id: str,
    device_name: str,
    data_source_type: str,
    include_daily: bool = True,
) -> DeviceComputationResult:
    avail = _availability(df)
    warnings: list[str] = []
    error: str | None = None

    method = "insufficient"
    quality = "insufficient"
    total_kwh: float | None = None
    total_hours = 0.0

    # Priority 1: direct cumulative meter delta
    if avail["energy_kwh"]:
        energy_series = df[["timestamp", "energy_kwh"]].dropna().sort_values("timestamp")
        if len(energy_series) >= 2:
            delta = float(energy_series["energy_kwh"].iloc[-1] - energy_series["energy_kwh"].iloc[0])
            if delta >= 0:
                method = "energy_kwh_direct"
                quality = "high"
                total_kwh = round(delta, 4)
                ts = energy_series["timestamp"]
                total_hours = float((ts.iloc[-1] - ts.iloc[0]).total_seconds() / 3600.0)
            else:
                warnings.append(
                    "energy_kwh appears non-monotonic for selected period; falling back to computed method"
                )

    # Priority 2: integrate power
    if total_kwh is None and avail["power"]:
        ts_sec, power_kw = _series_with_time(df, "power")
        val, hrs = _integrate_kwh(ts_sec, power_kw)
        if val is not None:
            method = "power_integration"
            quality = "medium"
            total_kwh = round(val, 4)
            total_hours = hrs

    # Priority 3/4: derive from V*I*PF then integrate
    if total_kwh is None and avail["current"] and avail["voltage"]:
        sub = df[["timestamp", "current", "voltage"]].copy()
        if avail["power_factor"]:
            sub["power_factor"] = pd.to_numeric(df["power_factor"], errors="coerce")
            sub = sub.dropna(subset=["power_factor"])
            sub["derived_power_kw"] = (sub["current"] * sub["voltage"] * sub["power_factor"]) / 1000.0
            method = "derived_power_v_i_pf"
            quality = "medium"
        else:
            sub["derived_power_kw"] = (sub["current"] * sub["voltage"]) / 1000.0
            method = "derived_power_v_i_pf1"
            quality = "low"
            warnings.append(
                "Power factor not available — estimated at 1.0. Actual consumption may differ."
            )

        sub = sub.dropna(subset=["derived_power_kw"]).sort_values("timestamp")
        ts_sec = (sub["timestamp"].astype("int64") / 1e9).to_numpy()
        power_kw = sub["derived_power_kw"].to_numpy(dtype=float)
        val, hrs = _integrate_kwh(ts_sec, power_kw)
        if val is not None:
            total_kwh = round(val, 4)
            total_hours = hrs

    # Priority 5: insufficient
    if total_kwh is None and avail["current"] and not avail["voltage"]:
        method = "insufficient_current_only"
        quality = "insufficient"
        error = "Insufficient telemetry — voltage required for energy calculation"

    if total_kwh is None and error is None:
        method = "insufficient_missing_fields"
        quality = "insufficient"
        error = "Insufficient telemetry — need one of: energy_kwh, power, or (current + voltage)"

    # Peak demand
    peak_demand_kw: float | None = None
    peak_timestamp: str | None = None
    if avail["power"]:
        sub = df[["timestamp", "power"]].dropna()
        if not sub.empty:
            idx = sub["power"].astype(float).idxmax()
            peak_demand_kw = round(float(sub.loc[idx, "power"]), 4)
            peak_timestamp = sub.loc[idx, "timestamp"].isoformat()
    elif avail["current"] and avail["voltage"]:
        sub = df[["timestamp", "current", "voltage"]].copy()
        if avail["power_factor"]:
            sub["power_factor"] = pd.to_numeric(df["power_factor"], errors="coerce")
            sub["derived_kw"] = (sub["current"] * sub["voltage"] * sub["power_factor"]) / 1000.0
        else:
            sub["derived_kw"] = (sub["current"] * sub["voltage"]) / 1000.0
            warnings.append(
                "Peak demand uses apparent power because power factor is unavailable."
            )
        sub = sub.dropna(subset=["derived_kw"])
        if not sub.empty:
            idx = sub["derived_kw"].astype(float).idxmax()
            peak_demand_kw = round(float(sub.loc[idx, "derived_kw"]), 4)
            peak_timestamp = sub.loc[idx, "timestamp"].isoformat()

    average_load_kw: float | None = None
    load_factor_pct: float | None = None
    load_factor_band: str | None = None
    if total_kwh is not None and total_hours > 0:
        average_load_kw = round(total_kwh / total_hours, 4)
    if average_load_kw is not None and peak_demand_kw and peak_demand_kw > 0:
        load_factor_pct = round((average_load_kw / peak_demand_kw) * 100.0, 2)
        load_factor_band = _load_factor_band(load_factor_pct)

    # Daily breakdown (uses same priority logic per day)
    daily_breakdown: list[dict[str, Any]] = []
    if include_daily and not df.empty:
        day_groups = df.groupby(df["timestamp"].dt.date)
        for day, day_df in day_groups:
            day_result = _compute_from_df(
                day_df.reset_index(drop=True),
                device_id=device_id,
                device_name=device_name,
                data_source_type=data_source_type,
                include_daily=False,
            )
            daily_breakdown.append(
                {
                    "date": str(day),
                    "energy_kwh": day_result.total_kwh,
                    "peak_demand_kw": day_result.peak_demand_kw,
                    "average_load_kw": day_result.average_load_kw,
                    "quality": day_result.quality,
                    "method": day_result.method,
                    "warnings": day_result.warnings,
                }
            )

    power_factor = None
    if avail["power_factor"]:
        pf = pd.to_numeric(df["power_factor"], errors="coerce").dropna()
        if not pf.empty:
            avg_pf = float(pf.mean())
            min_pf = float(pf.min())
            if avg_pf < 0.85:
                status = "poor"
                recommendation = "Install capacitor banks to improve power factor above 0.95"
            elif avg_pf < 0.92:
                status = "moderate"
                recommendation = "Consider power factor correction"
            else:
                status = "good"
                recommendation = None
            power_factor = {
                "average": round(avg_pf, 4),
                "min": round(min_pf, 4),
                "status": status,
                "recommendation": recommendation,
            }

    reactive = None
    reactive_field = "kvar" if avail["kvar"] else "reactive_power" if avail["reactive_power"] else None
    if reactive_field:
        ts_sec, kvar_vals = _series_with_time(df, reactive_field)
        total_kvarh, _ = _integrate_kwh(ts_sec, kvar_vals)
        if total_kvarh is not None:
            ratio = None
            if total_kwh and total_kwh > 0:
                ratio = round(float(total_kvarh / total_kwh), 4)
            reactive = {
                "total_kvarh": round(float(total_kvarh), 4),
                "reactive_ratio": ratio,
                "field_used": reactive_field,
            }

    return DeviceComputationResult(
        device_id=device_id,
        device_name=device_name,
        data_source_type=data_source_type,
        availability=avail,
        method=method,
        quality=quality,
        warnings=warnings,
        error=error,
        total_kwh=total_kwh,
        peak_demand_kw=peak_demand_kw,
        peak_timestamp=peak_timestamp,
        average_load_kw=average_load_kw,
        load_factor_pct=load_factor_pct,
        load_factor_band=load_factor_band,
        total_hours=round(total_hours, 4),
        daily_breakdown=daily_breakdown,
        power_factor=power_factor,
        reactive=reactive,
    )


def compute_device_report(
    rows: list[dict[str, Any]],
    device_id: str,
    device_name: str,
    data_source_type: str,
) -> DeviceComputationResult:
    df = _to_df(rows)
    if df.empty:
        return DeviceComputationResult(
            device_id=device_id,
            device_name=device_name,
            data_source_type=data_source_type,
            availability={},
            method="no_data",
            quality="insufficient",
            warnings=[],
            error="No telemetry data available for selected period",
            total_kwh=None,
            peak_demand_kw=None,
            peak_timestamp=None,
            average_load_kw=None,
            load_factor_pct=None,
            load_factor_band=None,
            total_hours=0.0,
            daily_breakdown=[],
            power_factor=None,
            reactive=None,
        )
    return _compute_from_df(df, device_id, device_name, data_source_type)
