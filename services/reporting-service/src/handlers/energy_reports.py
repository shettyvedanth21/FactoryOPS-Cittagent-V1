from datetime import datetime, timedelta, date
from uuid import uuid4
from typing import Optional
import asyncio
import hashlib
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_db, AsyncSessionLocal
from src.models import EnergyReport, ReportType, ReportStatus
from src.schemas.requests import ConsumptionReportRequest
from src.schemas.responses import ReportResponse
from src.repositories.report_repository import ReportRepository
from src.tasks.report_task import run_consumption_report

router = APIRouter(tags=["energy-reports"])


async def _run_consumption_report_with_timeout(report_id: str, task_params: dict) -> None:
    try:
        await asyncio.wait_for(
            run_consumption_report(report_id, task_params),
            timeout=max(1, settings.REPORT_JOB_TIMEOUT_SECONDS),
        )
    except asyncio.TimeoutError:
        async with AsyncSessionLocal() as db:
            repo = ReportRepository(db)
            await repo.update_report(
                report_id,
                status="failed",
                progress=100,
                error_code="JOB_TIMEOUT",
                error_message=f"Report exceeded timeout ({settings.REPORT_JOB_TIMEOUT_SECONDS}s)",
            )


async def resolve_all_devices(tenant_id: str) -> list[str]:
    """
    Resolve 'all' device selection to actual device IDs.
    Returns ALL devices - let the engine decide if telemetry exists.
    No filtering by device type at this layer.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"Resolving 'all' devices for tenant: {tenant_id}")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{settings.DEVICE_SERVICE_URL}/api/v1/devices"
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to device service: {e}")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "DEVICE_SERVICE_UNAVAILABLE",
                    "message": f"Cannot connect to device service: {str(e)}"
                }
            )
        
        if response.status_code != 200:
            logger.error(f"Device service returned {response.status_code}")
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "DEVICE_SERVICE_ERROR",
                    "message": f"Device service returned status {response.status_code}"
                }
            )
        
        data = response.json()
        devices = data if isinstance(data, list) else data.get("data", [])
        
        logger.info(f"Device service returned {len(devices)} total devices")
        
        all_device_ids = [d.get("device_id") for d in devices if d.get("device_id")]
        
        logger.info(f"Resolved {len(all_device_ids)} devices for tenant {tenant_id}")
        
        return all_device_ids


def normalize_dates_to_utc(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """
    Normalize dates to UTC with day-boundary alignment.
    - Floor start to 00:00:00 UTC
    - Ceil end to 23:59:59.999999 UTC
    Returns tuple of (start_datetime, end_datetime)
    """
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())
    return start_dt, end_dt


def validate_date_duration_seconds(start_dt: datetime, end_dt: datetime, min_seconds: int = 86400) -> bool:
    """
    Validate duration using seconds instead of days to avoid timezone drift.
    min_seconds defaults to 86400 (24 hours).
    """
    duration_seconds = (end_dt - start_dt).total_seconds()
    return duration_seconds >= min_seconds


async def validate_device_for_reporting(device_id: str) -> dict:
    """
    Validate device exists.
    Returns device data if valid.
    Raises HTTPException if invalid.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(
                f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}"
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "DEVICE_SERVICE_UNAVAILABLE",
                    "message": f"Cannot connect to device service: {str(e)}"
                }
            )
        
        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "DEVICE_NOT_FOUND",
                    "message": f"Device '{device_id}' not found. Please verify the device ID."
                }
            )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "DEVICE_SERVICE_ERROR",
                    "message": f"Device service returned status {response.status_code}"
                }
            )
        
        data = response.json()
        if isinstance(data, dict) and "data" in data:
            device_data = data["data"]
        else:
            device_data = data
        
        return device_data


@router.post("/consumption", response_model=ReportResponse)
async def create_energy_consumption_report(
    request: ConsumptionReportRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("="*60)
    logger.info("ENERGY REPORT REQUEST RECEIVED")
    logger.info(f"  start_date: {request.start_date}")
    logger.info(f"  end_date: {request.end_date}")
    logger.info(f"  device_id: {request.device_id}")
    logger.info(f"  tenant_id: {request.tenant_id}")
    logger.info("="*60)
    
    request_device_id = (request.device_id or "").strip()
    if not request_device_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "VALIDATION_ERROR", "message": "device_id is required"}
        )

    device_ids: list[str] = []
    if request_device_id.upper() == "ALL":
        logger.info("Received 'all' device selection, resolving to actual device IDs")
        resolved_ids = await resolve_all_devices(request.tenant_id)
        
        if not resolved_ids:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "NO_VALID_DEVICES",
                    "message": "No energy-capable devices found for this tenant."
                }
            )
        
        device_ids = resolved_ids
        logger.info(f"Resolved device IDs: {device_ids}")
    else:
        await validate_device_for_reporting(request_device_id)
        device_ids = [request_device_id]
    
    start_dt, end_dt = normalize_dates_to_utc(request.start_date, request.end_date)
    duration_seconds = (end_dt - start_dt).total_seconds()
    
    logger.info("="*60)
    logger.info("DATE NORMALIZATION RESULTS")
    logger.info(f"  Original start: {request.start_date}")
    logger.info(f"  Original end: {request.end_date}")
    logger.info(f"  UTC start_dt: {start_dt}")
    logger.info(f"  UTC end_dt: {end_dt}")
    logger.info(f"  Duration seconds: {duration_seconds}")
    logger.info(f"  Duration days: {duration_seconds / 86400}")
    logger.info("="*60)
    
    if not validate_date_duration_seconds(start_dt, end_dt):
        logger.error(f"Date validation FAILED: duration {duration_seconds} seconds < 86400")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_DATE_RANGE",
                "message": f"Date range must be at least 24 hours apart. Current: {duration_seconds/86400:.1f} days"
            }
        )
    
    repo = ReportRepository(db)
    dedup_payload = {
        "tenant_id": request.tenant_id,
        "report_type": "consumption",
        "device_id": request_device_id.upper() if request_device_id.upper() == "ALL" else request_device_id,
        "resolved_device_ids": sorted(device_ids),
        "start_date": str(request.start_date),
        "end_date": str(request.end_date),
        "report_name": request.report_name or "",
    }
    dedup_signature = hashlib.sha256(
        json.dumps(dedup_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    duplicate = await repo.find_active_duplicate(
        tenant_id=request.tenant_id,
        report_type="consumption",
        dedup_signature=dedup_signature,
    )
    if duplicate:
        dup_status = duplicate.status.value if hasattr(duplicate.status, "value") else str(duplicate.status)
        return ReportResponse(
            report_id=duplicate.report_id,
            status=dup_status,
            created_at=duplicate.created_at.isoformat() if duplicate.created_at else datetime.utcnow().isoformat(),
            estimated_completion_seconds=15,
        )
    
    report_id = str(uuid4())
    
    params = request.model_dump()
    params["start_date"] = str(params["start_date"])
    params["end_date"] = str(params["end_date"])
    params["resolved_device_ids"] = device_ids
    params["dedup_signature"] = dedup_signature
    
    await repo.create_report(
        report_id=report_id,
        tenant_id=request.tenant_id,
        report_type="consumption",
        params=params
    )

    task_params = {
        "tenant_id": request.tenant_id,
        "device_id": request_device_id.upper() if request_device_id.upper() == "ALL" else request_device_id,
        "resolved_device_ids": device_ids,
        "start_date": str(request.start_date),
        "end_date": str(request.end_date),
        "report_name": request.report_name,
    }
    background_tasks.add_task(_run_consumption_report_with_timeout, report_id, task_params)
    
    return ReportResponse(
        report_id=report_id,
        status="processing",
        created_at=datetime.utcnow().isoformat(),
        estimated_completion_seconds=15,
    )
