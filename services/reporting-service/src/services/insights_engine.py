from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _to_ist_label(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def generate_report_insights(
    per_device: list[dict[str, Any]],
    overall_total_kwh: float,
    currency: str | None,
) -> list[str]:
    insights: list[str] = []

    valid_devices = [d for d in per_device if isinstance(d.get("total_kwh"), (int, float))]

    if valid_devices and overall_total_kwh > 0:
        top = max(valid_devices, key=lambda d: float(d.get("total_kwh", 0.0)))
        top_kwh = float(top.get("total_kwh", 0.0))
        pct = (top_kwh / overall_total_kwh) * 100.0
        insights.append(
            f"{top.get('device_name', top.get('device_id'))} consumed {pct:.1f}% of total energy ({top_kwh:.2f} kWh)."
        )

    peak_candidates = [d for d in per_device if d.get("peak_demand_kw") is not None]
    if peak_candidates:
        peak_dev = max(peak_candidates, key=lambda d: float(d.get("peak_demand_kw") or 0.0))
        peak_ts = _parse_iso(peak_dev.get("peak_timestamp"))
        peak_kw = float(peak_dev.get("peak_demand_kw") or 0.0)
        if peak_ts:
            insights.append(
                f"Peak demand of {peak_kw:.2f} kW occurred on {_to_ist_label(peak_ts)}."
            )

    lf_values = [d.get("load_factor_pct") for d in per_device if d.get("load_factor_pct") is not None]
    if lf_values:
        avg_lf = sum(float(v) for v in lf_values) / len(lf_values)
        if avg_lf < 30:
            band = "poor"
        elif avg_lf <= 70:
            band = "moderate"
        else:
            band = "good"
        insights.append(f"Average load factor is {avg_lf:.1f}% — {band} utilization.")

    all_days: list[dict[str, Any]] = []
    for d in per_device:
        for day in d.get("daily_breakdown", []) or []:
            all_days.append({
                "device_id": d.get("device_id"),
                "device_name": d.get("device_name"),
                "date": day.get("date"),
                "cost": day.get("cost") if isinstance(day.get("cost"), (int, float)) else None,
                "energy_kwh": day.get("energy_kwh"),
            })

    cost_days = [x for x in all_days if x.get("cost") is not None]
    if cost_days:
        high = max(cost_days, key=lambda x: float(x.get("cost") or 0.0))
        insights.append(
            f"Highest cost day: {high.get('date')} at {float(high.get('cost') or 0.0):.2f} {currency or ''}."
        )

    quality_warnings: list[str] = []
    for d in per_device:
        for w in d.get("warnings", []) or []:
            quality_warnings.append(f"{d.get('device_name', d.get('device_id'))}: {w}")
        if d.get("quality") == "insufficient" and d.get("error"):
            quality_warnings.append(
                f"{d.get('device_name', d.get('device_id'))}: {d.get('error')}"
            )

    insights.extend(quality_warnings[:3])
    return insights[:8]
