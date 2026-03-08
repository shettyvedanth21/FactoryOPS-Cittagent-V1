from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional
import math

IST = ZoneInfo("Asia/Kolkata")


CURRENT_ALIASES = ["current", "phase_current", "i_l1", "current_l1", "current_l2", "current_l3"]
VOLTAGE_ALIASES = ["voltage", "voltage_l1", "v_l1", "voltage_l2", "v_l2", "voltage_l3", "v_l3"]
PF_ALIASES = ["power_factor", "pf"]
POWER_ALIASES = ["power", "active_power", "kw"]
ENERGY_ALIASES = ["energy_kwh", "kwh", "energy"]


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


@dataclass
class _Interval:
    ts: datetime
    duration_sec: float
    current: Optional[float]
    voltage: Optional[float]
    power_kw: Optional[float]
    pf: Optional[float]
    energy_kwh: Optional[float]


def _normalize_key(k: str) -> str:
    return k.strip().lower().replace("-", "_")


def _is_numeric_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return False
        return True
    except Exception:
        return False


def _find_field(row: dict[str, Any], aliases: list[str], contains_token: Optional[str] = None) -> Optional[str]:
    norm = {_normalize_key(k): k for k in row.keys()}
    for a in aliases:
        if a in norm and _is_numeric_value(row.get(norm[a])):
            return norm[a]
    if contains_token:
        for nk in sorted(norm.keys()):
            ok = norm[nk]
            if contains_token in nk and _is_numeric_value(row.get(ok)):
                return ok
    return None


def _extract_current(row: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    phase_keys = []
    for k in row.keys():
        nk = _normalize_key(k)
        if "current_l" in nk or nk.startswith("i_l"):
            phase_keys.append(k)
    if phase_keys:
        vals = [float(row[k]) for k in phase_keys if _is_numeric_value(row.get(k))]
        if vals:
            return max(vals), ",".join(phase_keys)

    key = _find_field(row, CURRENT_ALIASES, contains_token="current")
    if key and _is_numeric_value(row.get(key)):
        return float(row[key]), key
    return None, None


def _extract_voltage(row: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    phase_keys = []
    for k in row.keys():
        nk = _normalize_key(k)
        if "voltage_l" in nk or nk.startswith("v_l"):
            phase_keys.append(k)
    if phase_keys:
        vals = [float(row[k]) for k in phase_keys if _is_numeric_value(row.get(k))]
        if vals:
            return sum(vals) / len(vals), ",".join(phase_keys)

    key = _find_field(row, VOLTAGE_ALIASES, contains_token="voltage")
    if key and _is_numeric_value(row.get(key)):
        return float(row[key]), key
    return None, None


def _extract_pf(row: dict[str, Any]) -> Optional[float]:
    key = _find_field(row, PF_ALIASES, contains_token="power_factor")
    if key and _is_numeric_value(row.get(key)):
        return float(row[key])
    return None


def _extract_power_kw(row: dict[str, Any]) -> tuple[Optional[float], Optional[str], str, bool]:
    key = _find_field(row, POWER_ALIASES)
    if not key or not _is_numeric_value(row.get(key)):
        return None, None, "unknown", False
    val = float(row[key])
    nk = _normalize_key(key)
    if nk in {"power", "active_power"}:
        return val / 1000.0, key, "W", True
    if nk == "kw":
        return val, key, "kW", False
    return val, key, "unknown", False


def _extract_energy_kwh(row: dict[str, Any]) -> Optional[float]:
    key = _find_field(row, ENERGY_ALIASES, contains_token="energy")
    if key and _is_numeric_value(row.get(key)):
        return float(row[key])
    return None


def _safe_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt
    except Exception:
        return None


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


def _calc_total_energy(intervals: list[_Interval]) -> tuple[float, str, bool, str, list[str]]:
    warnings: list[str] = []
    pf_estimated = False

    energy_values = [x.energy_kwh for x in intervals if x.energy_kwh is not None]
    if len(energy_values) >= 2:
        delta = max(0.0, energy_values[-1] - energy_values[0])
        return round(delta, 6), "energy_delta", False, "high", warnings

    has_power = any(x.power_kw is not None for x in intervals)
    if has_power:
        series = [(x.ts, float(x.power_kw)) for x in intervals if x.power_kw is not None]
        total = 0.0
        saw_zero_gap = False
        saw_negative_gap = False
        for idx in range(1, len(series)):
            dt_sec = (series[idx][0] - series[idx - 1][0]).total_seconds()
            if dt_sec < 0:
                saw_negative_gap = True
                continue
            if dt_sec == 0:
                saw_zero_gap = True
                continue
            dt_h = dt_sec / 3600.0
            total += ((series[idx - 1][1] + series[idx][1]) / 2.0) * dt_h
        if saw_negative_gap:
            warnings.append("NON_MONOTONIC_TIMESTAMPS: non-monotonic samples skipped during integration")
        if saw_zero_gap:
            warnings.append("TIMESTAMP_GAP_SKIPPED: duplicate timestamp samples skipped during integration")
        return round(max(0.0, total), 6), "power_integration", False, "medium", warnings

    has_v_i = any(x.voltage is not None and x.current is not None for x in intervals)
    if has_v_i:
        series: list[tuple[datetime, float]] = []
        total = 0.0
        for x in intervals:
            if x.current is None or x.voltage is None:
                continue
            pf = x.pf if x.pf is not None else 1.0
            if x.pf is None:
                pf_estimated = True
            kw = (x.current * x.voltage * pf) / 1000.0
            series.append((x.ts, kw))

        saw_zero_gap = False
        saw_negative_gap = False
        for idx in range(1, len(series)):
            dt_sec = (series[idx][0] - series[idx - 1][0]).total_seconds()
            if dt_sec < 0:
                saw_negative_gap = True
                continue
            if dt_sec == 0:
                saw_zero_gap = True
                continue
            dt_h = dt_sec / 3600.0
            total += ((series[idx - 1][1] + series[idx][1]) / 2.0) * dt_h
        if saw_negative_gap:
            warnings.append("NON_MONOTONIC_TIMESTAMPS: non-monotonic samples skipped during integration")
        if saw_zero_gap:
            warnings.append("TIMESTAMP_GAP_SKIPPED: duplicate timestamp samples skipped during integration")

        quality = "low" if pf_estimated else "medium"
        if pf_estimated:
            warnings.append("Power factor missing for part/all telemetry; PF assumed as 1.0")
        return round(max(0.0, total), 6), "derived", pf_estimated, quality, warnings

    warnings.append("Insufficient telemetry for energy calculation (need power or voltage+current)")
    return 0.0, "insufficient", False, "insufficient", warnings


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

    sorted_rows = sorted(rows, key=lambda x: _safe_dt(x.get("timestamp") or x.get("_time")) or datetime.min)
    intervals: list[_Interval] = []
    current_field_used: Optional[str] = None

    saw_zero_gap = False
    saw_negative_gap = False
    for i, row in enumerate(sorted_rows):
        ts = _safe_dt(row.get("timestamp") or row.get("_time"))
        if ts is None:
            continue

        if i < len(sorted_rows) - 1:
            n_ts = _safe_dt(sorted_rows[i + 1].get("timestamp") or sorted_rows[i + 1].get("_time"))
            duration_sec = (n_ts - ts).total_seconds() if n_ts else 0.0
            if duration_sec < 0:
                saw_negative_gap = True
                duration_sec = 0.0
            elif duration_sec == 0:
                saw_zero_gap = True
        else:
            duration_sec = 0.0

        current, current_src = _extract_current(row)
        voltage, _ = _extract_voltage(row)
        pf = _extract_pf(row)
        power_kw, power_src, p_unit_in, p_norm = _extract_power_kw(row)
        energy_kwh = _extract_energy_kwh(row)
        if current_field_used is None and current_src:
            current_field_used = current_src
        if power_src and power_unit_input == "unknown":
            power_unit_input = p_unit_in
            normalization_applied = p_norm

        intervals.append(
            _Interval(
                ts=ts,
                duration_sec=max(0.0, duration_sec),
                current=current,
                voltage=voltage,
                power_kw=power_kw,
                pf=pf,
                energy_kwh=energy_kwh,
            )
        )

    if current_field_used is None:
        warnings.append("No current parameter detected; idle/load precision reduced")
    if normalization_applied:
        warnings.append("POWER_UNIT_ASSUMED_WATTS: normalized power/active_power to kW")
    if saw_negative_gap:
        warnings.append("NON_MONOTONIC_TIMESTAMPS: non-monotonic samples skipped during integration")
    if saw_zero_gap:
        warnings.append("TIMESTAMP_GAP_SKIPPED: duplicate timestamp samples skipped during integration")

    total_energy_kwh, method, pf_estimated, energy_quality, method_warnings = _calc_total_energy(intervals)
    idle_status = "configured"

    def _compute_idle_metrics(use_threshold: Optional[float]):
        idle_duration = 0
        idle_energy = 0.0
        idle_samples: list[float] = []
        offhours_energy = 0.0
        pf_estimated_local = pf_estimated

        for i in intervals:
            if i.duration_sec <= 0:
                continue
            state = detect_state(i.current, i.voltage, use_threshold)
            derived_power = i.power_kw
            this_pf_estimated = False
            if derived_power is None and i.current is not None and i.voltage is not None:
                pfv = i.pf if i.pf is not None else 1.0
                derived_power = (i.current * i.voltage * pfv) / 1000.0
                this_pf_estimated = i.pf is None

            if state == "idle":
                idle_duration += int(i.duration_sec)
                if derived_power is not None:
                    idle_energy += derived_power * (i.duration_sec / 3600.0)
                    idle_samples.append(derived_power)
                    if this_pf_estimated:
                        pf_estimated_local = True

            if derived_power is not None and _is_offhours(i.ts, shifts):
                offhours_energy += derived_power * (i.duration_sec / 3600.0)

        return idle_duration, idle_energy, idle_samples, offhours_energy, pf_estimated_local

    has_current_voltage = any(x.current is not None and x.voltage is not None for x in intervals)

    if threshold is None:
        idle_status = "needs_configuration"
        warnings.append("IDLE_THRESHOLD_NOT_CONFIGURED: idle threshold is required for idle waste calculation")
        idle_duration_sec = 0
        idle_energy_kwh = 0.0
        idle_kw_samples: list[float] = []
        offhours_energy_kwh = 0.0
        idle_quality = "insufficient"
        standby_quality = "insufficient"
    else:
        idle_duration_sec, idle_energy_kwh, idle_kw_samples, offhours_energy_kwh, pf_estimated = _compute_idle_metrics(
            threshold
        )
        idle_quality = "high" if has_current_voltage else "insufficient"
        if not has_current_voltage:
            warnings.append("IDLE_TELEMETRY_MISSING: current/voltage telemetry required for idle detection")
        if idle_duration_sec == 0:
            idle_status = "not_detected"
        standby_quality = "high" if idle_kw_samples or idle_status == "not_detected" else "insufficient"

    idle_energy_kwh = round(max(0.0, idle_energy_kwh), 6)
    standby_power_kw = round(sum(idle_kw_samples) / len(idle_kw_samples), 6) if idle_kw_samples else 0.0
    standby_energy_kwh = round(idle_energy_kwh, 6) if idle_kw_samples else 0.0

    idle_cost = round(idle_energy_kwh * tariff_rate, 2) if tariff_rate is not None else None
    standby_cost = round((standby_energy_kwh or 0.0) * tariff_rate, 2) if tariff_rate is not None and idle_kw_samples else None
    total_cost = round(total_energy_kwh * tariff_rate, 2) if tariff_rate is not None else None

    offhours_energy_kwh = round(max(0.0, offhours_energy_kwh), 6)
    if not shifts:
        offhours_energy_out = None
        offhours_cost = None
    else:
        offhours_energy_out = offhours_energy_kwh
        offhours_cost = round(offhours_energy_kwh * tariff_rate, 2) if tariff_rate is not None else None

    warnings = _fmt_warnings(warnings, method_warnings)

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
        offhours_energy_kwh=offhours_energy_out,
        offhours_cost=offhours_cost,
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

    total_waste_cost = sum(r.idle_cost or 0.0 for r in results)
    if total_waste_cost > 0:
        worst = max(results, key=lambda x: x.idle_cost or 0.0)
        share = ((worst.idle_cost or 0.0) / total_waste_cost) * 100 if total_waste_cost else 0
        insights.append(f"{worst.device_name} accounts for {share:.0f}% of total idle waste cost")

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

    if not insights:
        insights.append("No significant wastage pattern detected for selected scope and date range")

    return insights
