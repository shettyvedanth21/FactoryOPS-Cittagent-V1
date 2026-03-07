import logging
import traceback
from datetime import datetime
from typing import Any

import httpx

from src.config import settings
from src.database import AsyncSessionLocal
from src.repositories.report_repository import ReportRepository
from src.repositories.settings_repository import SettingsRepository
from src.services.influx_reader import influx_reader
from src.services.report_engine import compute_device_report
from src.services.insights_engine import generate_report_insights
from src.services import (
    calculate_energy,
    calculate_demand,
)
from src.pdf.builder import generate_consumption_pdf, generate_comparison_pdf
from src.storage.minio_client import minio_client
from src.utils.serialization import clean_for_json, extract_engine_data


logger = logging.getLogger(__name__)


def is_error(result: dict) -> bool:
    return isinstance(result, dict) and result.get("success") is False


async def run_consumption_report(report_id: str, params: dict) -> None:
    async with AsyncSessionLocal() as db:
        repo = ReportRepository(db)
        settings_repo = SettingsRepository(db)
        
        try:
            await repo.update_report(report_id, status="processing", progress=5)

            tenant_id = params.get("tenant_id", "default")
            start_date_str = params.get("start_date")
            end_date_str = params.get("end_date")
            request_device_id = params.get("device_id")
            resolved_device_ids = params.get("resolved_device_ids", [])

            if isinstance(start_date_str, str):
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            else:
                start_date = start_date_str

            if isinstance(end_date_str, str):
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            else:
                end_date = end_date_str

            if not start_date or not end_date:
                await repo.update_report(
                    report_id,
                    status="failed",
                    error_code="INVALID_PARAMS",
                    error_message="Missing required start_date/end_date"
                )
                return

            async with httpx.AsyncClient(timeout=30.0) as client:
                if resolved_device_ids:
                    device_ids = [str(x) for x in resolved_device_ids]
                elif isinstance(request_device_id, str) and request_device_id.upper() == "ALL":
                    resp = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices")
                    payload = resp.json() if resp.status_code == 200 else {}
                    items = payload if isinstance(payload, list) else payload.get("data", [])
                    device_ids = [d.get("device_id") for d in items if d.get("device_id")]
                elif isinstance(request_device_id, str) and request_device_id.strip():
                    device_ids = [request_device_id.strip()]
                else:
                    # Scheduler/backward internal params support
                    device_ids = [d for d in params.get("device_ids", []) if d]

                if not device_ids:
                    await repo.update_report(
                        report_id,
                        status="failed",
                        error_code="NO_VALID_DEVICES",
                        error_message="No devices available for report generation",
                    )
                    return

                await repo.update_report(report_id, progress=15)

                start_dt = datetime.combine(start_date, datetime.min.time())
                end_dt = datetime.combine(end_date, datetime.max.time())

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

                per_device: list[dict[str, Any]] = []
                all_warnings: list[str] = []

                for idx, device_id in enumerate(device_ids):
                    device_resp = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}")
                    if device_resp.status_code != 200:
                        per_device.append(
                            {
                                "device_id": device_id,
                                "device_name": device_id,
                                "data_source_type": "metered",
                                "quality": "insufficient",
                                "method": "device_not_found",
                                "error": f"Device lookup failed: {device_id}",
                                "warnings": [],
                                "total_kwh": None,
                                "peak_demand_kw": None,
                                "peak_timestamp": None,
                                "average_load_kw": None,
                                "load_factor_pct": None,
                                "load_factor_band": None,
                                "total_hours": 0.0,
                                "daily_breakdown": [],
                                "availability": {},
                                "power_factor": None,
                                "reactive": None,
                            }
                        )
                        continue

                    device_payload = device_resp.json()
                    device_data = device_payload.get("data", {}) if isinstance(device_payload, dict) else {}
                    device_name = device_data.get("device_name", device_id)
                    data_source_type = str(device_data.get("data_source_type") or "metered")

                    rows = await influx_reader.query_telemetry(
                        device_id=device_id,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        fields=fields,
                    )
                    device_result = compute_device_report(
                        rows=rows,
                        device_id=device_id,
                        device_name=device_name,
                        data_source_type=data_source_type,
                    )
                    device_dict = clean_for_json(device_result.__dict__)
                    for w in device_result.warnings:
                        all_warnings.append(f"{device_name}: {w}")
                    if device_result.error:
                        all_warnings.append(f"{device_name}: {device_result.error}")
                    per_device.append(device_dict)

                    progress = 15 + int(((idx + 1) / max(len(device_ids), 1)) * 45)
                    await repo.update_report(report_id, progress=min(progress, 60))

                total_kwh = round(
                    sum(float(d.get("total_kwh") or 0.0) for d in per_device if d.get("total_kwh") is not None),
                    4,
                )

                peak_candidates = [d for d in per_device if d.get("peak_demand_kw") is not None]
                peak_demand_kw = None
                peak_timestamp = None
                if peak_candidates:
                    peak_row = max(peak_candidates, key=lambda d: float(d.get("peak_demand_kw") or 0.0))
                    peak_demand_kw = peak_row.get("peak_demand_kw")
                    peak_timestamp = peak_row.get("peak_timestamp")

                total_hours = float(max((end_dt - start_dt).total_seconds() / 3600.0, 0.0))
                average_load_kw = round((total_kwh / total_hours), 4) if total_hours > 0 else None
                load_factor_pct = (
                    round((average_load_kw / float(peak_demand_kw)) * 100.0, 2)
                    if average_load_kw is not None and peak_demand_kw and float(peak_demand_kw) > 0
                    else None
                )

                if load_factor_pct is None:
                    load_factor_band = None
                elif load_factor_pct < 30:
                    load_factor_band = "poor"
                elif load_factor_pct <= 70:
                    load_factor_band = "moderate"
                else:
                    load_factor_band = "good"

                await repo.update_report(report_id, progress=70)

                # Tariff source of truth: settings.tariff_config only
                tariff_row = await settings_repo.get_tariff()
                tariff_rate_used = float(tariff_row.rate) if tariff_row else None
                tariff_currency = (tariff_row.currency or "INR") if tariff_row else "INR"
                tariff_fetched_at = datetime.utcnow().isoformat()

                total_cost = None
                if tariff_rate_used is not None:
                    total_cost = round(total_kwh * tariff_rate_used, 2)
                else:
                    all_warnings.append("Tariff not configured — cost calculations skipped")

                # Add cost into per-day rows
                for device in per_device:
                    for day in device.get("daily_breakdown", []) or []:
                        e = day.get("energy_kwh")
                        if tariff_rate_used is not None and isinstance(e, (int, float)):
                            day["cost"] = round(float(e) * tariff_rate_used, 2)
                        else:
                            day["cost"] = None

                overall_quality = "high"
                quality_rank = {"high": 0, "medium": 1, "low": 2, "insufficient": 3}
                for d in per_device:
                    q = d.get("quality", "insufficient")
                    if quality_rank.get(q, 3) > quality_rank.get(overall_quality, 0):
                        overall_quality = q

                insights = generate_report_insights(
                    per_device=per_device,
                    overall_total_kwh=total_kwh,
                    currency=tariff_currency,
                )

                await repo.update_report(report_id, progress=85)

                # Flatten day-wise total across devices for chart
                by_day: dict[str, float] = {}
                for d in per_device:
                    for row in d.get("daily_breakdown", []) or []:
                        date_key = str(row.get("date"))
                        if isinstance(row.get("energy_kwh"), (int, float)):
                            by_day[date_key] = by_day.get(date_key, 0.0) + float(row["energy_kwh"])
                daily_series = [{"date": k, "kwh": round(v, 4)} for k, v in sorted(by_day.items())]

                pdf_payload = {
                    "report_id": report_id,
                    "device_label": "All Machines" if len(device_ids) > 1 else per_device[0].get("device_name", device_ids[0]),
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "total_kwh": total_kwh,
                    "peak_demand_kw": peak_demand_kw,
                    "peak_timestamp": peak_timestamp,
                    "average_load_kw": average_load_kw,
                    "load_factor_pct": load_factor_pct,
                    "load_factor_band": load_factor_band,
                    "total_cost": total_cost,
                    "currency": tariff_currency,
                    "tariff_rate_used": tariff_rate_used,
                    "daily_series": daily_series,
                    "per_device": per_device,
                    "insights": insights,
                    "warnings": all_warnings,
                    "overall_quality": overall_quality,
                    "tariff_fetched_at": tariff_fetched_at,
                    "generated_at": datetime.utcnow().isoformat(),
                }

                pdf_bytes = generate_consumption_pdf(clean_for_json(pdf_payload))
                await repo.update_report(report_id, progress=95)

                s3_key = f"reports/{tenant_id}/{report_id}.pdf"
                minio_client.upload_pdf(pdf_bytes, s3_key)

                result_json = {
                    "schema_version": "3.0",
                    "report_id": report_id,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "device_scope": "ALL" if len(device_ids) > 1 else device_ids[0],
                    "summary": {
                        "total_kwh": total_kwh,
                        "peak_demand_kw": peak_demand_kw,
                        "peak_timestamp": peak_timestamp,
                        "average_load_kw": average_load_kw,
                        "load_factor_pct": load_factor_pct,
                        "load_factor_band": load_factor_band,
                        "total_cost": total_cost,
                        "currency": tariff_currency,
                    },
                    "data_quality": {
                        "overall": overall_quality,
                        "per_device": {
                            d["device_id"]: {
                                "quality": d.get("quality"),
                                "method": d.get("method"),
                                "warnings": d.get("warnings", []),
                                "error": d.get("error"),
                            }
                            for d in per_device
                        },
                    },
                    "warnings": all_warnings,
                    "insights": insights,
                    "daily_series": daily_series,
                    "devices": per_device,
                    "tariff_rate_used": tariff_rate_used,
                    "tariff_currency": tariff_currency,
                    "tariff_fetched_at": tariff_fetched_at,
                }

                await repo.update_report(
                    report_id,
                    status="completed",
                    progress=100,
                    result_json=clean_for_json(result_json),
                    s3_key=s3_key,
                    completed_at=datetime.utcnow(),
                )
            
        except Exception as e:
            logger.error(f"Report {report_id} failed: {traceback.format_exc()}")
            await repo.update_report(
                report_id,
                status="failed",
                error_code="INTERNAL_ERROR",
                error_message=str(e)
            )


async def run_comparison_report(report_id: str, params: dict) -> None:
    from src.services.comparison_engine import calculate_comparison
    
    async with AsyncSessionLocal() as db:
        repo = ReportRepository(db)
        
        try:
            await repo.update_report(report_id, status="processing", progress=10)
            
            tenant_id = params.get("tenant_id")
            comparison_type = params.get("comparison_type")
            
            if comparison_type == "machine_vs_machine":
                device_a = params.get("machine_a_id")
                device_b = params.get("machine_b_id")
                start_date_str = params.get("start_date")
                end_date_str = params.get("end_date")
                
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                
                await repo.update_report(report_id, progress=20)
                
                async with httpx.AsyncClient() as client:
                    resp_a = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_a}")
                    resp_b = await client.get(f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_b}")
                    
                    device_a_data = resp_a.json()
                    device_b_data = resp_b.json()
                    
                    if isinstance(device_a_data, dict) and "data" in device_a_data:
                        device_a_data = device_a_data["data"]
                    if isinstance(device_b_data, dict) and "data" in device_b_data:
                        device_b_data = device_b_data["data"]
                
                await repo.update_report(report_id, progress=30)
                
                start_dt = datetime.combine(start_date, datetime.min.time())
                end_dt = datetime.combine(end_date, datetime.max.time())
                
                fields = ["power", "voltage", "current", "power_factor"]
                
                rows_a = await influx_reader.query_telemetry(
                    device_id=device_a, start_dt=start_dt, end_dt=end_dt, fields=fields
                )
                rows_b = await influx_reader.query_telemetry(
                    device_id=device_b, start_dt=start_dt, end_dt=end_dt, fields=fields
                )
                
                if not rows_a or not rows_b:
                    await repo.update_report(
                        report_id,
                        status="failed",
                        error_code="NO_TELEMETRY_DATA",
                        error_message="Comparative analysis cannot be generated. No telemetry data available for one or both devices in the selected period. Please try again later."
                    )
                    return
                
                await repo.update_report(report_id, progress=40)
                
                phase_type_a = device_a_data.get("phase_type", "single")
                phase_type_b = device_b_data.get("phase_type", "single")
                
                energy_a = calculate_energy(rows_a, phase_type_a)
                energy_b = calculate_energy(rows_b, phase_type_b)
                
                if is_error(energy_a) or is_error(energy_b):
                    await repo.update_report(
                        report_id,
                        status="failed",
                        error_code="ENERGY_CALCULATION_ERROR",
                        error_message="Failed to calculate energy for one or both devices"
                    )
                    return
                
                await repo.update_report(report_id, progress=60)
                
                energy_data_a = extract_engine_data(energy_a)
                energy_data_b = extract_engine_data(energy_b)
                power_series_a = energy_data_a.get("power_series", [])
                power_series_b = energy_data_b.get("power_series", [])
                
                demand_a = calculate_demand(power_series_a, settings.DEMAND_WINDOW_MINUTES)
                demand_b = calculate_demand(power_series_b, settings.DEMAND_WINDOW_MINUTES)
                
                await repo.update_report(report_id, progress=70)
                
                comparison_result = calculate_comparison(
                    energy_a, energy_b, demand_a, demand_b,
                    device_a_data.get("device_name", device_a),
                    device_b_data.get("device_name", device_b)
                )
                
                if is_error(comparison_result):
                    await repo.update_report(
                        report_id,
                        status="failed",
                        error_code=comparison_result.get("error_code", "COMPARISON_ERROR"),
                        error_message=comparison_result.get("error_message", "Comparison calculation failed")
                    )
                    return
                
                await repo.update_report(report_id, progress=80)
                
                settings_repo = SettingsRepository(db)
                settings_tariff = await settings_repo.get_tariff()
                tariff_dict = {
                    "energy_rate_per_kwh": float(settings_tariff.rate) if settings_tariff else None,
                    "currency": str(settings_tariff.currency) if settings_tariff else "INR",
                }
                
                await repo.update_report(report_id, progress=90)
                
                pdf_data = {
                    "report_id": report_id,
                    "device_a_name": device_a_data.get("device_name", device_a),
                    "device_b_name": device_b_data.get("device_name", device_b),
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "comparison": comparison_result.get("data", {}).get("metrics", {}),
                    "winner": comparison_result.get("data", {}).get("winner"),
                    "insights": comparison_result.get("data", {}).get("insights", []),
                    "currency": tariff_dict.get("currency", "INR")
                }
                
                pdf_bytes = generate_comparison_pdf(clean_for_json(pdf_data))
                
                s3_key = f"reports/{tenant_id}/{report_id}.pdf"
                minio_client.upload_pdf(pdf_bytes, s3_key)
                
                await repo.update_report(
                    report_id,
                    status="completed",
                    progress=100,
                    result_json=clean_for_json(comparison_result),
                    s3_key=s3_key,
                    completed_at=datetime.utcnow()
                )
                
            else:
                await repo.update_report(
                    report_id,
                    status="failed",
                    error_code="NOT_IMPLEMENTED",
                    error_message="Period vs Period comparison not yet implemented"
                )
                
        except Exception as e:
            logger.error(f"Comparison report {report_id} failed: {traceback.format_exc()}")
            await repo.update_report(
                report_id,
                status="failed",
                error_code="INTERNAL_ERROR",
                error_message=str(e)
            )
