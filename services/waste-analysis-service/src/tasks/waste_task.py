from __future__ import annotations

import asyncio
import logging
import os
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
from src.utils import clean_for_json

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


def _effective_concurrency(configured: int) -> int:
    cpu = max(1, int(os.cpu_count() or 1))
    safe_upper = max(4, cpu * 4)
    return max(1, min(int(configured), safe_upper))


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


def _to_db_summary(x: dict) -> dict:
    return {
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
        "offhours_duration_sec": x.get("offhours_duration_sec"),
        "offhours_skipped_reason": x.get("offhours_skipped_reason"),
        "offhours_pf_estimated": x.get("offhours_pf_estimated", False),
        "overconsumption_duration_sec": x.get("overconsumption_duration_sec"),
        "overconsumption_kwh": x.get("overconsumption_kwh"),
        "overconsumption_cost": x.get("overconsumption_cost"),
        "overconsumption_skipped_reason": x.get("overconsumption_skipped_reason"),
        "overconsumption_pf_estimated": x.get("overconsumption_pf_estimated", False),
        "unoccupied_duration_sec": x.get("unoccupied_duration_sec"),
        "unoccupied_energy_kwh": x.get("unoccupied_energy_kwh"),
        "unoccupied_cost": x.get("unoccupied_cost"),
        "unoccupied_skipped_reason": x.get("unoccupied_skipped_reason"),
        "unoccupied_pf_estimated": x.get("unoccupied_pf_estimated", False),
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
            overconsumption_threshold_by_device: dict[str, float | None] = {}
            config_warnings: list[str] = []
            skipped_devices: list[dict] = []

            eff_conc = _effective_concurrency(settings.WASTE_DEVICE_CONCURRENCY)
            logger.info(
                "waste_device_concurrency_resolved configured=%s effective=%s cpu_count=%s",
                int(settings.WASTE_DEVICE_CONCURRENCY),
                eff_conc,
                max(1, int(os.cpu_count() or 1)),
            )
            cfg_sem = asyncio.Semaphore(eff_conc)

            async def _load_device_config(
                d: dict,
            ) -> tuple[
                str,
                float | None,
                list[dict],
                float | None,
            ]:
                device_id = d.get("device_id")
                if not device_id:
                    return "", None, [], None
                async with cfg_sem:
                    threshold, shifts, waste_cfg = await asyncio.gather(
                        device_client.get_idle_config(device_id),
                        device_client.get_shift_config(device_id),
                        device_client.get_waste_config(device_id),
                    )

                overconsumption_threshold = waste_cfg.get("overconsumption_current_threshold_a")
                overconsumption_threshold = (
                    float(overconsumption_threshold)
                    if overconsumption_threshold is not None
                    else None
                )

                return (
                    str(device_id),
                    threshold,
                    shifts,
                    overconsumption_threshold,
                )

            cfg_tasks = [asyncio.create_task(_load_device_config(d)) for d in devices]
            for fut in asyncio.as_completed(cfg_tasks):
                (
                    device_id,
                    threshold,
                    shifts,
                    overconsumption_threshold,
                ) = await fut
                if not device_id:
                    continue
                threshold_by_device[device_id] = threshold
                shifts_by_device[device_id] = shifts
                overconsumption_threshold_by_device[device_id] = overconsumption_threshold
                if threshold is None:
                    config_warnings.append(f"{device_id}: idle threshold not configured (idle category reduced)")
                if overconsumption_threshold is None:
                    config_warnings.append(f"{device_id}: overconsumption threshold not configured (category skipped)")

            tariff = await tariff_cache.get()
            await repo.update_job(job_id, progress_pct=10, stage="Fetching tariff configuration...")

            start_dt = datetime.combine(start_date, time.min)
            end_dt = datetime.combine(end_date, time.max)

            results = []
            warnings: list[str] = list(config_warnings)
            n_devices = max(1, len(devices))
            dev_sem = asyncio.Semaphore(eff_conc)

            async def _process_device(d: dict):
                device_id = d.get("device_id")
                if not device_id:
                    return None
                device_name = d.get("device_name") or device_id
                data_source_type = d.get("data_source_type") or "metered"
                async with dev_sem:
                    rows = await influx_reader.query_telemetry(
                        device_id=device_id,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        fields=TELEMETRY_FIELDS,
                    )
                threshold = threshold_by_device.get(device_id)
                shifts = shifts_by_device.get(device_id, [])
                overconsumption_threshold = overconsumption_threshold_by_device.get(device_id)
                res = compute_device_waste(
                    device_id=device_id,
                    device_name=device_name,
                    data_source_type=str(data_source_type),
                    rows=rows,
                    threshold=threshold,
                    overconsumption_threshold=overconsumption_threshold,
                    tariff_rate=tariff.rate,
                    shifts=shifts,
                )
                return device_name, device_id, res

            proc_tasks = [asyncio.create_task(_process_device(d)) for d in devices]
            processed = 0
            for fut in asyncio.as_completed(proc_tasks):
                out = await fut
                if out is None:
                    continue
                device_name, device_id, res = out
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
                processed += 1
                if processed == 1 or processed == n_devices or (processed % max(1, n_devices // 20) == 0):
                    await repo.update_job(
                        job_id,
                        progress_pct=min(80, 10 + int((processed / n_devices) * 65)),
                        stage=f"Loading telemetry and computing... ({processed} of {n_devices})",
                    )

            total_idle_kwh = round(sum(r.idle_energy_kwh for r in results), 6)
            total_idle_seconds = sum(r.idle_duration_sec for r in results)
            total_energy_kwh = round(sum(r.total_energy_kwh for r in results), 6)
            total_energy_cost = None if tariff.rate is None else round(sum((r.total_cost or 0.0) for r in results), 2)
            total_waste_cost = None if tariff.rate is None else round(
                sum(
                    (r.idle_cost or 0.0)
                    + (r.offhours_cost or 0.0)
                    + (r.overconsumption_cost or 0.0)
                    for r in results
                ),
                2,
            )
            worst_device = "N/A"
            if results:
                worst = max(
                    results,
                    key=lambda x: (x.idle_cost or 0.0)
                    + (x.offhours_cost or 0.0)
                    + (x.overconsumption_cost or 0.0),
                )
                worst_device = worst.device_name

            insights = summarize_insights(results, tariff.currency)

            device_summaries = []
            for r in results:
                device_total_waste_cost = round(
                    (r.idle_cost or 0.0)
                    + (r.offhours_cost or 0.0)
                    + (r.overconsumption_cost or 0.0),
                    2,
                ) if tariff.rate is not None else None
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
                        "total_energy_cost": r.total_cost,
                        "total_energy_cost_inr": r.total_cost,
                        "total_waste_cost": device_total_waste_cost,
                        "total_waste_cost_inr": device_total_waste_cost,
                        "offhours_energy_kwh": r.offhours_energy_kwh,
                        "offhours_cost": r.offhours_cost,
                        "offhours_duration_sec": r.offhours_duration_sec,
                        "offhours_skipped_reason": r.offhours_skipped_reason,
                        "offhours_pf_estimated": r.offhours_pf_estimated,
                        "overconsumption_duration_sec": r.overconsumption_duration_sec,
                        "overconsumption_kwh": r.overconsumption_energy_kwh,
                        "overconsumption_cost": r.overconsumption_cost,
                        "overconsumption_skipped_reason": r.overconsumption_skipped_reason,
                        "overconsumption_pf_estimated": r.overconsumption_pf_estimated,
                        "unoccupied_duration_sec": r.unoccupied_duration_sec,
                        "unoccupied_energy_kwh": r.unoccupied_energy_kwh,
                        "unoccupied_cost": r.unoccupied_cost,
                        "unoccupied_skipped_reason": r.unoccupied_skipped_reason,
                        "unoccupied_pf_estimated": r.unoccupied_pf_estimated,
                        "off_hours": {
                            "duration_sec": r.offhours_duration_sec,
                            "energy_kwh": r.offhours_energy_kwh,
                            "cost": r.offhours_cost,
                            "skipped_reason": r.offhours_skipped_reason,
                            "pf_estimated": r.offhours_pf_estimated,
                            "config_source": "shift_config",
                        },
                        "overconsumption": {
                            "duration_sec": r.overconsumption_duration_sec,
                            "energy_kwh": r.overconsumption_energy_kwh,
                            "cost": r.overconsumption_cost,
                            "skipped_reason": r.overconsumption_skipped_reason,
                            "pf_estimated": r.overconsumption_pf_estimated,
                            "config_source": r.overconsumption_config_source,
                            "config_used": r.overconsumption_config_used,
                        },
                        "unoccupied_running": {
                            "duration_sec": r.unoccupied_duration_sec,
                            "energy_kwh": r.unoccupied_energy_kwh,
                            "cost": r.unoccupied_cost,
                            "skipped_reason": r.unoccupied_skipped_reason,
                            "pf_estimated": r.unoccupied_pf_estimated,
                            "config_source": r.unoccupied_config_source,
                            "config_used": r.unoccupied_config_used,
                        },
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
                "total_energy_cost_inr": total_energy_cost,
                "total_waste_cost": total_waste_cost,
                "total_waste_cost_inr": total_waste_cost,
                "total_idle_cost_inr": None if tariff.rate is None else round(sum((r.idle_cost or 0.0) for r in results), 2),
                "offhours_total_cost_inr": None if tariff.rate is None else round(sum((r.offhours_cost or 0.0) for r in results), 2),
                "overconsumption_total_cost_inr": None if tariff.rate is None else round(sum((r.overconsumption_cost or 0.0) for r in results), 2),
                "worst_device": worst_device,
                "device_summaries": device_summaries,
                "warnings": sorted(set(warnings)),
                "insights": insights,
                "quality_gate_passed": quality_gate_passed,
                "quality_failures": quality_failures,
                "skipped_devices": skipped_devices,
                "estimation_used": False,
                "calculation_version": "waste_v2_exclusive",
                "aggregation_policy": "mutually_exclusive",
                "diagnostic_only_categories": ["standby"],
            }

            invariant_checks = {"waste_le_total_energy": True}
            if total_waste_cost is not None and total_energy_cost is not None:
                invariant_checks["waste_le_total_energy"] = total_waste_cost <= (total_energy_cost + 0.01)
            result_payload["invariant_checks"] = invariant_checks

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

            db_summaries = [_to_db_summary(x) for x in device_summaries]

            if settings.WASTE_STRICT_QUALITY_GATE and not quality_gate_passed:
                await repo.replace_device_summaries_chunked(
                    job_id,
                    summaries=db_summaries,
                    batch_size=settings.WASTE_DB_BATCH_SIZE,
                )
                await repo.update_job(
                    job_id,
                    status="failed",
                    error_code="QUALITY_GATE_FAILED",
                    progress_pct=100,
                    stage="Quality gate failed",
                    error_message="Quality gate failed: one or more devices are low/insufficient quality",
                    result_json=clean_for_json(result_payload),
                    completed_at=datetime.utcnow(),
                )
                return

            await repo.update_job(job_id, progress_pct=88, stage="Generating PDF...")
            pdf_bytes = generate_waste_pdf(result_payload)
            s3_key = f"waste-reports/{job_id}/waste_report_{uuid4().hex[:8]}.pdf"
            minio_client.upload_pdf(pdf_bytes, s3_key)
            download_url = minio_client.get_presigned_url(s3_key)

            await repo.replace_device_summaries_chunked(
                job_id,
                summaries=db_summaries,
                batch_size=settings.WASTE_DB_BATCH_SIZE,
            )

            await repo.update_job(
                job_id,
                status="completed",
                progress_pct=100,
                stage="Complete ✓",
                result_json=clean_for_json(result_payload),
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
