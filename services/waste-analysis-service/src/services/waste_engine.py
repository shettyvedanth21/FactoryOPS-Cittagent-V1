from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional
from src.services.telemetry_normalizer import (
    NormalizedInterval,
    build_normalized_intervals,
)

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_PF = 0.85


@dataclass
class DeviceWasteResult:
    device_id: str
    device_name: str
    data_source_type: str
    idle_duration_sec: int
    idle_energy_kwh: float
    idle_cost: Optional[float]
    standby_power_kw: Optional[float]
    standby_energy_kwh: Optional[float]
    standby_cost: Optional[float]
    total_energy_kwh: float
    total_cost: Optional[float]
    offhours_energy_kwh: Optional[float]
    offhours_cost: Optional[float]
    offhours_duration_sec: Optional[int]
    offhours_skipped_reason: Optional[str]
    offhours_pf_estimated: bool
    overconsumption_duration_sec: Optional[int]
    overconsumption_energy_kwh: Optional[float]
    overconsumption_cost: Optional[float]
    overconsumption_skipped_reason: Optional[str]
    overconsumption_pf_estimated: bool
    overconsumption_config_source: Optional[str]
    overconsumption_config_used: Optional[dict[str, Any]]
    unoccupied_duration_sec: Optional[int]
    unoccupied_energy_kwh: Optional[float]
    unoccupied_cost: Optional[float]
    unoccupied_skipped_reason: Optional[str]
    unoccupied_pf_estimated: bool
    unoccupied_config_source: Optional[str]
    unoccupied_config_used: Optional[dict[str, Any]]
    data_quality: str
    pf_estimated: bool
    warnings: list[str]
    calculation_method: str
    idle_status: str
    energy_quality: str
    idle_quality: str
    standby_quality: str
    overall_quality: str
    power_unit_input: str
    power_unit_normalized_to: str
    normalization_applied: bool


_Interval = NormalizedInterval


def detect_state(current: Optional[float], voltage: Optional[float], threshold: Optional[float]) -> str:
    if current is None or voltage is None:
        return "unknown"
    if current <= 0 and voltage > 0:
        return "unloaded"
    if threshold is None:
        return "unknown" if current > 0 and voltage > 0 else "unknown"
    if 0 < current < threshold and voltage > 0:
        return "idle"
    if current >= threshold and voltage > 0:
        return "running"
    return "unknown"


def _quality_rank(quality: str) -> int:
    order = {"high": 3, "medium": 2, "low": 1, "insufficient": 0}
    return order.get(quality, 0)


def _overall_quality(*qualities: str) -> str:
    ranked = sorted(qualities, key=_quality_rank)
    return ranked[0] if ranked else "insufficient"


def _interval_energy_kwh(interval: _Interval) -> tuple[Optional[float], bool]:
    if interval.duration_sec <= 0:
        return 0.0, False
    duration_h = interval.duration_sec / 3600.0
    if interval.power_kw is not None and interval.power_kw >= 0:
        return max(0.0, interval.power_kw * duration_h), False
    if interval.current_a is not None and interval.voltage_v is not None:
        pf = interval.pf if interval.pf is not None else DEFAULT_PF
        kw = (interval.current_a * interval.voltage_v * pf) / 1000.0
        return max(0.0, kw * duration_h), interval.pf is None
    return None, False


def _calc_total_energy(intervals: list[_Interval]) -> tuple[float, str, bool, str, list[str]]:
    warnings: list[str] = []
    total_kwh = 0.0
    known_intervals = 0
    power_intervals = 0
    derived_intervals = 0
    pf_estimated = False

    for interval in intervals:
        energy, est = _interval_energy_kwh(interval)
        if energy is None:
            continue
        known_intervals += 1
        pf_estimated = pf_estimated or est
        if interval.power_kw is not None:
            power_intervals += 1
        else:
            derived_intervals += 1
        total_kwh += energy

    if known_intervals == 0:
        warnings.append("Insufficient telemetry for energy calculation (need power or voltage+current)")
        return 0.0, "insufficient", False, "insufficient", warnings

    if known_intervals < len([x for x in intervals if x.duration_sec > 0]):
        warnings.append("ENERGY_PARTIAL_COVERAGE: some intervals missing telemetry and were skipped")
    if pf_estimated:
        warnings.append("Power factor missing for part/all telemetry; PF assumed as 0.85")

    method = "interval_power" if power_intervals >= derived_intervals else "interval_derived"
    quality = "medium" if power_intervals > 0 else "low"
    return round(max(0.0, total_kwh), 6), method, pf_estimated, quality, warnings


def _is_offhours(ts: datetime, shifts: list[dict[str, Any]]) -> bool:
    if not shifts:
        return False
    local = ts.astimezone(IST)
    minutes = local.hour * 60 + local.minute
    dow = local.weekday()

    for s in shifts:
        day = s.get("day_of_week")
        if day is not None and day != dow:
            continue
        start = str(s.get("shift_start") or "00:00")
        end = str(s.get("shift_end") or "00:00")
        try:
            sh, sm = [int(v) for v in start.split(":")[:2]]
            eh, em = [int(v) for v in end.split(":")[:2]]
        except Exception:
            continue
        start_m = sh * 60 + sm
        end_m = eh * 60 + em
        if end_m <= start_m:
            in_shift = minutes >= start_m or minutes <= end_m
        else:
            in_shift = start_m <= minutes <= end_m
        if in_shift:
            return False
    return True


def _fmt_warnings(base: list[str], extra: list[str]) -> list[str]:
    merged = [w for w in (base + extra) if w]
    seen = set()
    out = []
    for w in merged:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def compute_device_waste(
    device_id: str,
    device_name: str,
    data_source_type: str,
    rows: list[dict[str, Any]],
    threshold: Optional[float],
    overconsumption_threshold: Optional[float],
    tariff_rate: Optional[float],
    shifts: list[dict[str, Any]],
) -> DeviceWasteResult:
    warnings: list[str] = []
    power_unit_input = "unknown"
    power_unit_normalized_to = "kW"
    normalization_applied = False
    if not rows:
        return DeviceWasteResult(
            device_id=device_id,
            device_name=device_name,
            data_source_type=data_source_type,
            idle_duration_sec=0,
            idle_energy_kwh=0.0,
            idle_cost=None,
            standby_power_kw=None,
            standby_energy_kwh=None,
            standby_cost=None,
            total_energy_kwh=0.0,
            total_cost=None,
            offhours_energy_kwh=None,
            offhours_cost=None,
            offhours_duration_sec=None,
            offhours_skipped_reason="No telemetry data",
            offhours_pf_estimated=False,
            overconsumption_duration_sec=None,
            overconsumption_energy_kwh=None,
            overconsumption_cost=None,
            overconsumption_skipped_reason="No telemetry data",
            overconsumption_pf_estimated=False,
            overconsumption_config_source=None,
            overconsumption_config_used=None,
            unoccupied_duration_sec=None,
            unoccupied_energy_kwh=None,
            unoccupied_cost=None,
            unoccupied_skipped_reason="No telemetry data",
            unoccupied_pf_estimated=False,
            unoccupied_config_source=None,
            unoccupied_config_used=None,
            data_quality="insufficient",
            pf_estimated=False,
            warnings=["No telemetry data in selected range"],
            calculation_method="insufficient",
            idle_status="unknown",
            energy_quality="insufficient",
            idle_quality="insufficient",
            standby_quality="insufficient",
            overall_quality="insufficient",
            power_unit_input=power_unit_input,
            power_unit_normalized_to=power_unit_normalized_to,
            normalization_applied=normalization_applied,
        )

    intervals, telemetry_meta = build_normalized_intervals(rows, max_gap_seconds=900.0)
    current_field_used = telemetry_meta.get("current_field_used")
    power_unit_input = telemetry_meta.get("power_unit_input") or power_unit_input
    normalization_applied = bool(telemetry_meta.get("normalization_applied"))

    if current_field_used is None:
        warnings.append("No current parameter detected; idle/load precision reduced")
    if normalization_applied:
        warnings.append("POWER_UNIT_ASSUMED_WATTS: normalized power/active_power to kW")
    if telemetry_meta.get("saw_negative_gap"):
        warnings.append("NON_MONOTONIC_TIMESTAMPS: non-monotonic samples skipped during integration")
    if telemetry_meta.get("saw_zero_gap"):
        warnings.append("TIMESTAMP_GAP_SKIPPED: duplicate timestamp samples skipped during integration")
    if telemetry_meta.get("saw_large_gap"):
        warnings.append("LARGE_TIMESTAMP_GAP_SKIPPED: intervals larger than 15 minutes were skipped")

    total_energy_kwh, method, pf_estimated, energy_quality, method_warnings = _calc_total_energy(intervals)
    idle_status = "configured"
    has_current_voltage = any(x.current_a is not None and x.voltage_v is not None for x in intervals)
    if threshold is None:
        idle_status = "needs_configuration"
        warnings.append("IDLE_THRESHOLD_NOT_CONFIGURED: idle threshold is required for idle waste calculation")
        idle_quality = "insufficient"
    else:
        idle_quality = "high" if has_current_voltage else "insufficient"
        if not has_current_voltage:
            warnings.append("IDLE_TELEMETRY_MISSING: current/voltage telemetry required for idle detection")

    over_skip_reason: Optional[str] = None
    if overconsumption_threshold is None:
        over_skip_reason = "Current threshold not configured for this device"

    off_skip_reason: Optional[str] = None
    if not shifts:
        off_skip_reason = "Shift not configured for this device"

    idle_duration_sec = 0
    idle_energy_kwh = 0.0
    idle_kw_samples: list[float] = []
    offhours_duration_sec = 0 if off_skip_reason is None else None
    offhours_energy_kwh = 0.0 if off_skip_reason is None else None
    over_duration_sec = 0 if over_skip_reason is None else None
    over_energy_kwh = 0.0 if over_skip_reason is None else None
    off_pf_estimated = False
    over_pf_estimated = False
    category_warnings: list[str] = []

    thr_idle = float(threshold) if threshold is not None else None
    thr_over = float(overconsumption_threshold) if overconsumption_threshold is not None else None

    for interval in intervals:
        if interval.duration_sec <= 0:
            continue

        base_energy, base_pf_est = _interval_energy_kwh(interval)
        pf_estimated = pf_estimated or base_pf_est
        duration_h = interval.duration_sec / 3600.0
        state = detect_state(interval.current_a, interval.voltage_v, thr_idle)
        running = (interval.power_kw is not None and interval.power_kw > 0) or (
            interval.current_a is not None and interval.current_a > (thr_idle or 0.0)
        )

        over_energy = 0.0
        over_known = False
        if thr_over is not None and interval.current_a is not None and interval.current_a > thr_over:
            if over_duration_sec is not None:
                over_duration_sec += int(interval.duration_sec)
            excess_current = interval.current_a - thr_over
            if interval.voltage_v is not None and interval.voltage_v > 0:
                excess_kw = (excess_current * interval.voltage_v * DEFAULT_PF) / 1000.0
                over_pf_estimated = True
                over_known = True
            elif interval.power_kw is not None and interval.power_kw > 0:
                excess_kw = interval.power_kw * (excess_current / max(interval.current_a, 1e-9))
                over_known = True
            else:
                category_warnings.append(
                    "OVERCONSUMPTION: Current exceeded threshold but voltage/power unavailable for some intervals"
                )
                excess_kw = 0.0
            if over_known:
                over_energy = max(0.0, excess_kw * duration_h)
                if base_energy is not None:
                    over_energy = min(over_energy, base_energy)
                if over_energy_kwh is not None:
                    over_energy_kwh += over_energy
            elif over_energy_kwh is not None:
                over_energy_kwh += 0.0

        remaining_energy = None if base_energy is None else max(0.0, base_energy - over_energy)

        if off_skip_reason is None and _is_offhours(interval.ts, shifts) and running:
            if offhours_duration_sec is not None:
                offhours_duration_sec += int(interval.duration_sec)
            if base_pf_est:
                off_pf_estimated = True
            if remaining_energy is not None and offhours_energy_kwh is not None:
                offhours_energy_kwh += remaining_energy
            elif running:
                category_warnings.append(
                    "OFF_HOURS: Running detected outside shift but energy unavailable for some intervals"
                )
        elif thr_idle is not None and state == "idle":
            idle_duration_sec += int(interval.duration_sec)
            if remaining_energy is not None:
                idle_energy_kwh += remaining_energy
                if duration_h > 0:
                    idle_kw_samples.append(remaining_energy / duration_h)

    if threshold is not None and idle_duration_sec == 0:
        idle_status = "not_detected"
    standby_quality = "high" if idle_kw_samples or idle_status == "not_detected" else "insufficient"

    idle_energy_kwh = round(max(0.0, idle_energy_kwh), 6)
    standby_power_kw = round(sum(idle_kw_samples) / len(idle_kw_samples), 6) if idle_kw_samples else 0.0
    standby_energy_kwh = round(idle_energy_kwh, 6) if idle_kw_samples else 0.0

    if offhours_energy_kwh is not None:
        offhours_energy_kwh = round(max(0.0, offhours_energy_kwh), 6)
    if over_energy_kwh is not None:
        over_energy_kwh = round(max(0.0, over_energy_kwh), 6)

    idle_cost = round(idle_energy_kwh * tariff_rate, 2) if tariff_rate is not None else None
    standby_cost = (
        round((standby_energy_kwh or 0.0) * tariff_rate, 2)
        if tariff_rate is not None and idle_kw_samples
        else None
    )
    total_cost = round(total_energy_kwh * tariff_rate, 2) if tariff_rate is not None else None
    offhours_cost = (
        round((offhours_energy_kwh or 0.0) * tariff_rate, 2)
        if tariff_rate is not None and offhours_energy_kwh is not None
        else None
    )
    overconsumption_cost = (
        round((over_energy_kwh or 0.0) * tariff_rate, 2)
        if tariff_rate is not None and over_energy_kwh is not None
        else None
    )

    if off_skip_reason is None and offhours_duration_sec == 0 and offhours_energy_kwh == 0:
        category_warnings.append("OFF_HOURS: No off-hours consumption detected")
    if over_skip_reason is None and over_duration_sec == 0 and over_energy_kwh == 0:
        category_warnings.append("OVERCONSUMPTION: No overconsumption detected in this period")

    warnings = _fmt_warnings(warnings, method_warnings + category_warnings)

    overall_quality = _overall_quality(energy_quality, idle_quality, standby_quality)

    return DeviceWasteResult(
        device_id=device_id,
        device_name=device_name,
        data_source_type=data_source_type,
        idle_duration_sec=idle_duration_sec,
        idle_energy_kwh=idle_energy_kwh,
        idle_cost=idle_cost,
        standby_power_kw=standby_power_kw,
        standby_energy_kwh=standby_energy_kwh,
        standby_cost=standby_cost,
        total_energy_kwh=round(total_energy_kwh, 6),
        total_cost=total_cost,
        offhours_energy_kwh=offhours_energy_kwh,
        offhours_cost=offhours_cost,
        offhours_duration_sec=offhours_duration_sec,
        offhours_skipped_reason=off_skip_reason,
        offhours_pf_estimated=off_pf_estimated,
        overconsumption_duration_sec=over_duration_sec,
        overconsumption_energy_kwh=over_energy_kwh,
        overconsumption_cost=overconsumption_cost,
        overconsumption_skipped_reason=over_skip_reason,
        overconsumption_pf_estimated=over_pf_estimated,
        overconsumption_config_source=("device_override" if overconsumption_threshold is not None else None),
        overconsumption_config_used=(
            {"threshold_a": round(float(overconsumption_threshold), 4)}
            if overconsumption_threshold is not None
            else None
        ),
        unoccupied_duration_sec=None,
        unoccupied_energy_kwh=None,
        unoccupied_cost=None,
        unoccupied_skipped_reason="Disabled by policy",
        unoccupied_pf_estimated=False,
        unoccupied_config_source=None,
        unoccupied_config_used=None,
        data_quality=overall_quality,
        pf_estimated=pf_estimated,
        warnings=warnings,
        calculation_method=method,
        idle_status=idle_status,
        energy_quality=energy_quality,
        idle_quality=idle_quality,
        standby_quality=standby_quality,
        overall_quality=overall_quality,
        power_unit_input=power_unit_input,
        power_unit_normalized_to=power_unit_normalized_to,
        normalization_applied=normalization_applied,
    )


def summarize_insights(results: list[DeviceWasteResult], currency: str) -> list[str]:
    insights: list[str] = []
    if not results:
        return insights

    total_waste_cost = sum(
        (r.idle_cost or 0.0)
        + (r.offhours_cost or 0.0)
        + (r.overconsumption_cost or 0.0)
        for r in results
    )
    if total_waste_cost > 0:
        worst = max(
            results,
            key=lambda x: (x.idle_cost or 0.0)
            + (x.offhours_cost or 0.0)
            + (x.overconsumption_cost or 0.0),
        )
        worst_cost = (
            (worst.idle_cost or 0.0)
            + (worst.offhours_cost or 0.0)
            + (worst.overconsumption_cost or 0.0)
        )
        share = (worst_cost / total_waste_cost) * 100 if total_waste_cost else 0
        insights.append(f"{worst.device_name} accounts for {share:.0f}% of total waste cost")

    savings_lines = []
    for r in results:
        if r.standby_power_kw and r.standby_power_kw > 0 and total_waste_cost >= 0:
            monthly = r.standby_power_kw * 30
            if monthly > 0.2:
                savings_lines.append((monthly, r.device_name))
    savings_lines.sort(reverse=True)
    for monthly, name in savings_lines[:3]:
        insights.append(f"Reducing {name} idle time by 1hr/day can save about {currency} {monthly:.0f}/month")

    high_standby = [r.device_name for r in sorted(results, key=lambda x: x.standby_power_kw or 0, reverse=True) if (r.standby_power_kw or 0) > 0.5]
    if high_standby:
        insights.append(f"Highest standby consumers: {', '.join(high_standby[:3])}")

    offhours_total = sum(r.offhours_cost or 0.0 for r in results)
    if offhours_total > 0:
        insights.append(f"Off-hours energy waste in selected period: {currency} {offhours_total:.0f}")

    overcons_total = sum(r.overconsumption_cost or 0.0 for r in results)
    if overcons_total > 0:
        insights.append(f"Overconsumption waste in selected period: {currency} {overcons_total:.0f}")

    if not insights:
        insights.append("No significant wastage pattern detected for selected scope and date range")

    return insights
