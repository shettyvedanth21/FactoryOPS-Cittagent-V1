from datetime import datetime
from uuid import uuid4
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_db, AsyncSessionLocal
from src.repositories import WasteRepository
from src.schemas import (
    WasteAnalysisRunRequest,
    WasteAnalysisRunResponse,
    WasteDownloadResponse,
    WasteHistoryResponse,
    WasteStatusResponse,
)
from src.storage.minio_client import minio_client
from src.tasks.waste_task import run_waste_analysis

router = APIRouter(tags=["waste-analysis"])


async def _run_waste_analysis_with_timeout(job_id: str, params: dict) -> None:
    try:
        await asyncio.wait_for(
            run_waste_analysis(job_id, params),
            timeout=max(1, settings.WASTE_JOB_TIMEOUT_SECONDS),
        )
    except asyncio.TimeoutError:
        async with AsyncSessionLocal() as session:
            repo = WasteRepository(session)
            await repo.update_job(
                job_id,
                status="failed",
                progress_pct=100,
                stage="Timed out",
                error_code="JOB_TIMEOUT",
                error_message=f"Waste analysis exceeded timeout ({settings.WASTE_JOB_TIMEOUT_SECONDS}s)",
                completed_at=datetime.utcnow(),
            )


def _to_utc_iso(ts: datetime | None) -> str | None:
    """Serialize UTC-naive DB timestamps as explicit UTC ISO strings."""
    if ts is None:
        return None
    # Job timestamps are persisted in UTC; append Z so clients never parse as local time.
    return ts.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


@router.post("/analysis/run", response_model=WasteAnalysisRunResponse, status_code=202)
async def run_analysis(
    request: WasteAnalysisRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if request.start_date > request.end_date:
        raise HTTPException(status_code=400, detail={"error": "VALIDATION_ERROR", "message": "start_date must be <= end_date"})

    if request.scope == "selected" and not request.device_ids:
        raise HTTPException(status_code=400, detail={"error": "VALIDATION_ERROR", "message": "device_ids required when scope=selected"})

    repo = WasteRepository(db)
    duplicate = await repo.find_active_duplicate(
        scope=request.scope,
        device_ids=request.device_ids,
        start_date=request.start_date,
        end_date=request.end_date,
        granularity=request.granularity,
    )
    if duplicate:
        return WasteAnalysisRunResponse(
            job_id=duplicate.id,
            status=duplicate.status.value if hasattr(duplicate.status, "value") else str(duplicate.status),
            estimated_completion_seconds=30,
        )

    job_id = str(uuid4())
    await repo.create_job(
        job_id=job_id,
        job_name=request.job_name,
        scope=request.scope,
        device_ids=request.device_ids,
        start_date=request.start_date,
        end_date=request.end_date,
        granularity=request.granularity,
    )

    background_tasks.add_task(
        _run_waste_analysis_with_timeout,
        job_id,
        {
            "scope": request.scope,
            "device_ids": request.device_ids,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "granularity": request.granularity,
        },
    )

    return WasteAnalysisRunResponse(job_id=job_id, status="pending", estimated_completion_seconds=30)


@router.get("/analysis/{job_id}/status", response_model=WasteStatusResponse)
async def get_status(job_id: str, db: AsyncSession = Depends(get_db)):
    repo = WasteRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return WasteStatusResponse(
        job_id=job.id,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        progress_pct=job.progress_pct,
        stage=job.stage,
        error_code=job.error_code,
        error_message=job.error_message,
    )


@router.get("/analysis/{job_id}/result")
async def get_result(job_id: str, db: AsyncSession = Depends(get_db)):
    repo = WasteRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.result_json is None:
        raise HTTPException(status_code=400, detail="Result not available")
    return job.result_json


@router.get("/analysis/{job_id}/download", response_model=WasteDownloadResponse)
async def get_download(job_id: str, db: AsyncSession = Depends(get_db)):
    repo = WasteRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.s3_key:
        raise HTTPException(status_code=404, detail="Report file not available")
    url = minio_client.get_presigned_url(job.s3_key)
    return WasteDownloadResponse(job_id=job.id, download_url=url, expires_in_seconds=900)


@router.get("/analysis/history", response_model=WasteHistoryResponse)
async def get_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    repo = WasteRepository(db)
    jobs = await repo.list_jobs(limit=limit, offset=offset)
    return WasteHistoryResponse(
        items=[
            {
                "job_id": j.id,
                "job_name": j.job_name,
                "status": j.status.value if hasattr(j.status, "value") else str(j.status),
                "error_code": j.error_code,
                "error_message": j.error_message,
                "created_at": _to_utc_iso(j.created_at),
                "completed_at": _to_utc_iso(j.completed_at),
                "progress_pct": j.progress_pct,
            }
            for j in jobs
        ]
    )
