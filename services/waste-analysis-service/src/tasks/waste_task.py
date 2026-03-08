from __future__ import annotations

import logging
from datetime import datetime, time
from uuid import uuid4

import httpx

from src.config import settings
from src.database import AsyncSessionLocal
from src.pdf.builder import generate_waste_pdf
from src.repositories import WasteRepository
from src.services import compute_device_waste, summarize_insights
from src.services.influx_reader import influx_reader
from src.services.remote_clients import device_client, tariff_cache
from src.storage.minio_client import minio_client

logger = logging.getLogger(__name__)

TELEMETRY_FIELDS = [
    "energy_kwh",
    "power",
    "current",
    "current_l1",
    "current_l2",
    "current_l3",
    "phase_current",
    "i_l1",
    "voltage",
    "voltage_l1",
    "voltage_l2",
    "voltage_l3",
    "power_factor",
    "pf",
]


async def _resolve_devices(scope: str, requested_ids: list[str] | None) -> list[dict]:
    if scope == "selected":
        out = []
        for device_id in requested_ids or []:
            d = await device_client.get_device(device_id)
            if d:
                out.append(d)
        return out
    return await device_client.list_devices()


def _duration_label(seconds: int) -> str:
    minutes = max(0, round(seconds / 60))
    hours = minutes // 60
    rem = minutes % 60
    if hours <= 0:
        return f"{rem} min"
    return f"{hours} hr {rem} min"


def _is_low_or_insufficient(quality: str | None) -> bool:
    return (quality or "").lower() in {"low", "insufficient"}


async def _find_reporting_reference_kwh(
    scope: str,
    selected_ids: list[str],
    start_date,
    end_date,
) -> float | None:
    tenant_id = "default"
    target_scope = "ALL" if scope == "all" else (selected_ids[0] if len(selected_ids) == 1 else None)
    if target_scope is None:
        return None
    async with httpx.AsyncClient(timeout=12.0) as client:
        hist = await client.get(
            f"{settings.REPORTING_SERVICE_URL}/api/reports/history",
            params={"tenant_id": tenant_id, "limit": 50, "report_type": "consumption"},
        )
        if hist.status_code != 200:
            return None
        reports = (hist.json() or {}).get("reports") or []
        for item in reports:
            if item.get("status") != "completed":
                continue
            rid = item.get("report_id")
            if not rid:
                continue
            res = await client.get(
                f"{settings.REPORTING_SERVICE_URL}/api/reports/{rid}/result",
                params={"tenant_id": tenant_id},
            )
            if res.status_code != 200:
                continue
            payload = res.json() or {}
            if payload.get("start_date") != start_date.isoformat():
                continue
            if payload.get("end_date") != end_date.isoformat():
                continue
            if str(payload.get("device_scope")) != target_scope:
                continue
            summary = payload.get("summary") or {}
            total_kwh = summary.get("total_kwh")
            if isinstance(total_kwh, (int, float)):
                return float(total_kwh)
    return None


async def run_waste_analysis(job_id: str, params: dict) -> None:
    async with AsyncSessionLocal() as db:
        repo = WasteRepository(db)

        try:
            await repo.update_job(job_id, status="running", progress_pct=5, stage="Fetching device list...")

            scope = params.get("scope", "all")
            start_date = datetime.strptime(params["start_date"], "%Y-%m-%d").date()
            end_date = datetime.strptime(params["end_date"], "%Y-%m-%d").date()
            granularity = params.get("granularity", "daily")
            selected = params.get("device_ids") or []

            devices = await _resolve_devices(scope, selected)
            if not devices:
                await repo.update_job(
                    job_id,
                    status="failed",
                    error_code="NO_DEVICES_FOUND",
                    progress_pct=100,
                    stage="Failed",
                    error_message="No devices available for analysis",
                    completed_at=datetime.utcnow(),
                )
                return

            await repo.update_job(job_id, progress_pct=8, stage="Validating configuration...")

            quality_failures: list[dict] = []
            threshold_by_device: dict[str, float | None] = {}
            shifts_by_device: dict[str, list[dict]] = {}
            for d in devices:
                device_id = d.get("device_id")
                if not device_id:
                    continue
                threshold = await device_client.get_idle_config(device_id)
                threshold_by_device[device_id] = threshold
                shifts_by_device[device_id] = await device_client.get_shift_config(device_id)
                if threshold is None:
                    quality_failures.append(
                        {
                            "device_id": device_id,
                            "metric": "idle",
                            "code": "IDLE_THRESHOLD_NOT_CONFIGURED",
                            "message": f"Idle threshold not configured for {device_id}",
                        }
                    )

            if settings.WASTE_STRICT_QUALITY_GATE and quality_failures:
                payload = {
                    "job_id": job_id,
                    "scope": scope,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "granularity": granularity,
                    "quality_gate_passed": False,
                    "quality_failures": quality_failures,
                    "estimation_used": False,
                    "device_summaries": [],
                    "warnings": [f"{f['device_id']}: {f['message']}" for f in quality_failures],
                }
                await repo.update_job(
                    job_id,
                    status="failed",
                    error_code="QUALITY_GATE_FAILED",
                    progress_pct=100,
                    stage="Quality gate failed",
                    error_message="Quality gate failed: configure idle threshold for all selected devices",
                    result_json=payload,
                    completed_at=datetime.utcnow(),
                )
                return

            tariff = await tariff_cache.get()
            await repo.update_job(job_id, progress_pct=10, stage="Fetching tariff configuration...")

            start_dt = datetime.combine(start_date, time.min)
            end_dt = datetime.combine(end_date, time.max)

            results = []
            warnings: list[str] = []
            for idx, d in enumerate(devices, start=1):
                device_id = d.get("device_id")
                if not device_id:
                    continue
                device_name = d.get("device_name") or device_id
                data_source_type = d.get("data_source_type") or "metered"

                await repo.update_job(
                    job_id,
                    progress_pct=min(80, 10 + int((idx / max(1, len(devices))) * 65)),
                    stage=f"Loading telemetry for {device_name}... ({idx} of {len(devices)})",
                )

                rows = await influx_reader.query_telemetry(
                    device_id=device_id,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    fields=TELEMETRY_FIELDS,
                )
                threshold = threshold_by_device.get(device_id)
                shifts = shifts_by_device.get(device_id, [])

                res = compute_device_waste(
                    device_id=device_id,
                    device_name=device_name,
                    data_source_type=str(data_source_type),
                    rows=rows,
                    threshold=threshold,
                    tariff_rate=tariff.rate,
                    shifts=shifts,
                )
                results.append(res)
                warnings.extend([f"{device_name}: {w}" for w in res.warnings])
                if _is_low_or_insufficient(res.overall_quality):
                    quality_failures.append(
                        {
                            "device_id": device_id,
                            "metric": "overall",
                            "code": "LOW_QUALITY_DATA" if res.overall_quality == "low" else "INSUFFICIENT_DATA",
                            "message": f"Device quality is {res.overall_quality}",
                        }
                    )

            total_idle_kwh = round(sum(r.idle_energy_kwh for r in results), 6)
            total_idle_seconds = sum(r.idle_duration_sec for r in results)
            total_energy_kwh = round(sum(r.total_energy_kwh for r in results), 6)
            total_energy_cost = None if tariff.rate is None else round(sum((r.total_cost or 0.0) for r in results), 2)
            total_waste_cost = None if tariff.rate is None else round(sum((r.idle_cost or 0.0) for r in results), 2)
            worst_device = "N/A"
            if results:
                worst = max(results, key=lambda x: x.idle_cost or 0.0)
                worst_device = worst.device_name

            insights = summarize_insights(results, tariff.currency)

            device_summaries = []
            for r in results:
                device_summaries.append(
                    {
                        "device_id": r.device_id,
                        "device_name": r.device_name,
                        "data_source_type": r.data_source_type,
                        "idle_duration_sec": r.idle_duration_sec,
                        "idle_duration_label": _duration_label(r.idle_duration_sec),
                        "idle_energy_kwh": r.idle_energy_kwh,
                        "idle_cost": r.idle_cost,
                        "standby_power_kw": r.standby_power_kw,
                        "standby_energy_kwh": r.standby_energy_kwh,
                        "standby_cost": r.standby_cost,
                        "total_energy_kwh": r.total_energy_kwh,
                        "total_cost": r.total_cost,
                        "offhours_energy_kwh": r.offhours_energy_kwh,
                        "offhours_cost": r.offhours_cost,
                        "data_quality": r.data_quality,
                        "energy_quality": r.energy_quality,
                        "idle_quality": r.idle_quality,
                        "standby_quality": r.standby_quality,
                        "overall_quality": r.overall_quality,
                        "idle_status": r.idle_status,
                        "power_unit_input": r.power_unit_input,
                        "power_unit_normalized_to": r.power_unit_normalized_to,
                        "normalization_applied": r.normalization_applied,
                        "pf_estimated": r.pf_estimated,
                        "warnings": r.warnings,
                        "calculation_method": r.calculation_method,
                    }
                )

            quality_gate_passed = len(quality_failures) == 0

            result_payload = {
                "job_id": job_id,
                "scope": scope,
                "scope_label": "All Devices" if scope == "all" else f"Selected Devices ({len(device_summaries)})",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "granularity": granularity,
                "tariff_rate_used": tariff.rate,
                "currency": tariff.currency,
                "tariff_stale": tariff.stale,
                "total_idle_kwh": total_idle_kwh,
                "total_idle_duration_sec": total_idle_seconds,
                "total_idle_label": _duration_label(total_idle_seconds),
                "total_energy_kwh": total_energy_kwh,
                "total_energy_cost": total_energy_cost,
                "total_waste_cost": total_waste_cost,
                "worst_device": worst_device,
                "device_summaries": device_summaries,
                "warnings": sorted(set(warnings)),
                "insights": insights,
                "quality_gate_passed": quality_gate_passed,
                "quality_failures": quality_failures,
                "estimation_used": False,
            }

            try:
                ref_kwh = await _find_reporting_reference_kwh(scope, selected, start_date, end_date)
                if ref_kwh is not None:
                    tolerance = max(0.5, 0.01 * ref_kwh)
                    delta = abs(total_energy_kwh - ref_kwh)
                    passed = delta <= tolerance
                    result_payload["parity_check"] = {
                        "checked": True,
                        "reference_source": "reporting-service:consumption",
                        "reference_total_kwh": round(ref_kwh, 6),
                        "waste_total_kwh": round(total_energy_kwh, 6),
                        "abs_delta_kwh": round(delta, 6),
                        "tolerance_kwh": round(tolerance, 6),
                        "passed": passed,
                    }
                    if not passed:
                        result_payload["warnings"].append(
                            "PARITY_CHECK_WARNING: waste vs consumption totals differ beyond tolerance"
                        )
                else:
                    result_payload["parity_check"] = {"checked": False}
            except Exception:
                result_payload["parity_check"] = {"checked": False}

            if settings.WASTE_STRICT_QUALITY_GATE and not quality_gate_passed:
                await repo.replace_device_summaries(
                    job_id,
                    summaries=[
                        {
                            "device_id": x["device_id"],
                            "device_name": x["device_name"],
                            "data_source_type": x["data_source_type"],
                            "idle_duration_sec": x["idle_duration_sec"],
                            "idle_energy_kwh": x["idle_energy_kwh"],
                            "idle_cost": x["idle_cost"],
                            "standby_power_kw": x["standby_power_kw"],
                            "standby_energy_kwh": x["standby_energy_kwh"],
                            "standby_cost": x["standby_cost"],
                            "total_energy_kwh": x["total_energy_kwh"],
                            "total_cost": x["total_cost"],
                            "offhours_energy_kwh": x["offhours_energy_kwh"],
                            "offhours_cost": x["offhours_cost"],
                            "data_quality": x["data_quality"],
                            "energy_quality": x["energy_quality"],
                            "idle_quality": x["idle_quality"],
                            "standby_quality": x["standby_quality"],
                            "overall_quality": x["overall_quality"],
                            "idle_status": x["idle_status"],
                            "pf_estimated": x["pf_estimated"],
                            "warnings": x["warnings"],
                            "calculation_method": x["calculation_method"],
                        }
                        for x in device_summaries
                    ],
                )
                await repo.update_job(
                    job_id,
                    status="failed",
                    error_code="QUALITY_GATE_FAILED",
                    progress_pct=100,
                    stage="Quality gate failed",
                    error_message="Quality gate failed: one or more devices are low/insufficient quality",
                    result_json=result_payload,
                    completed_at=datetime.utcnow(),
                )
                return

            await repo.update_job(job_id, progress_pct=88, stage="Generating PDF...")
            pdf_bytes = generate_waste_pdf(result_payload)
            s3_key = f"waste-reports/{job_id}/waste_report_{uuid4().hex[:8]}.pdf"
            minio_client.upload_pdf(pdf_bytes, s3_key)
            download_url = minio_client.get_presigned_url(s3_key)

            await repo.replace_device_summaries(
                job_id,
                summaries=[
                    {
                        "device_id": x["device_id"],
                        "device_name": x["device_name"],
                        "data_source_type": x["data_source_type"],
                        "idle_duration_sec": x["idle_duration_sec"],
                        "idle_energy_kwh": x["idle_energy_kwh"],
                        "idle_cost": x["idle_cost"],
                        "standby_power_kw": x["standby_power_kw"],
                        "standby_energy_kwh": x["standby_energy_kwh"],
                        "standby_cost": x["standby_cost"],
                        "total_energy_kwh": x["total_energy_kwh"],
                        "total_cost": x["total_cost"],
                        "offhours_energy_kwh": x["offhours_energy_kwh"],
                        "offhours_cost": x["offhours_cost"],
                        "data_quality": x["data_quality"],
                        "energy_quality": x["energy_quality"],
                        "idle_quality": x["idle_quality"],
                        "standby_quality": x["standby_quality"],
                        "overall_quality": x["overall_quality"],
                        "idle_status": x["idle_status"],
                        "pf_estimated": x["pf_estimated"],
                        "warnings": x["warnings"],
                        "calculation_method": x["calculation_method"],
                    }
                    for x in device_summaries
                ],
            )

            await repo.update_job(
                job_id,
                status="completed",
                progress_pct=100,
                stage="Complete ✓",
                result_json=result_payload,
                s3_key=s3_key,
                download_url=download_url,
                tariff_rate_used=tariff.rate,
                currency=tariff.currency,
                error_code=None,
                completed_at=datetime.utcnow(),
            )
        except Exception as exc:
            logger.exception("waste_analysis_failed job_id=%s", job_id)
            await repo.update_job(
                job_id,
                status="failed",
                error_code="INTERNAL_ERROR",
                progress_pct=100,
                stage="Failed",
                error_message=str(exc),
                completed_at=datetime.utcnow(),
            )
