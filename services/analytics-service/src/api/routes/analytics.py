"""Analytics API endpoints."""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

import aiohttp
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.dependencies import get_job_queue, get_result_repository
from src.config.settings import get_settings
from src.infrastructure.database import async_session_maker
from src.infrastructure.mysql_repository import MySQLResultRepository
from src.infrastructure.s3_client import S3Client
from src.models.schemas import (
    AnalyticsJobResponse,
    AnalyticsRequest,
    AnalyticsResultsResponse,
    AnalyticsType,
    FleetAnalyticsRequest,
    JobStatus,
    JobStatusResponse,
    SupportedModelsResponse,
)
from src.services.result_formatter import ResultFormatter
from src.services.result_repository import ResultRepository
from src.utils.exceptions import JobNotFoundError
from src.workers.job_queue import JobQueue

from src.services.dataset_service import DatasetService

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/run",
    response_model=AnalyticsJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_analytics(
    request: AnalyticsRequest,
    app_request: Request,
    job_queue: JobQueue = Depends(get_job_queue),
) -> AnalyticsJobResponse:
    """
    Submit a new analytics job.

    The job will be queued and processed asynchronously.
    Use the returned job_id to check status and retrieve results.
    """
    job_id = str(uuid4())

    logger.info(
        "analytics_job_submitted",
        job_id=job_id,
        analysis_type=request.analysis_type.value,
        model_name=request.model_name,
        device_id=request.device_id,
    )

    await job_queue.submit_job(
        job_id=job_id,
        request=request,
    )
    if not hasattr(app_request.app.state, "pending_jobs"):
        app_request.app.state.pending_jobs = {}
    app_request.app.state.pending_jobs[job_id] = {
        "created_at": datetime.now(timezone.utc),
        "message": "Job queued successfully",
    }

    return AnalyticsJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Job queued successfully",
    )


def _default_model_for(analysis_type: str) -> str:
    if analysis_type == AnalyticsType.ANOMALY.value:
        return "isolation_forest"
    return "random_forest"


async def _fetch_all_device_ids() -> List[str]:
    # Device-service contract in this stack.
    url = "http://device-service:8000/api/v1/devices"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                devices = data if isinstance(data, list) else data.get("devices", data.get("data", []))
                ids = [d.get("id") or d.get("device_id") for d in devices if isinstance(d, dict)]
                return [d for d in ids if d]
    except Exception:
        return []


async def _trigger_export(device_id: str) -> None:
    settings = get_settings()
    url = f"{settings.data_export_service_url}/api/v1/exports/run"
    payload = {"device_id": device_id}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise RuntimeError(f"export trigger failed ({resp.status}): {text}")


async def _wait_for_dataset_key(
    s3_client: S3Client,
    expected_key: str,
) -> bool:
    settings = get_settings()
    delay = settings.data_readiness_initial_delay_seconds
    attempts = settings.data_readiness_poll_attempts
    for i in range(max(1, attempts)):
        if await s3_client.object_exists(expected_key):
            return True
        await asyncio.sleep(delay * (2 ** i))
    return await s3_client.object_exists(expected_key)


async def _ensure_device_ready(
    s3_client: S3Client,
    dataset_service: DatasetService,
    device_id: str,
    start_time: datetime,
    end_time: datetime,
) -> tuple[str, Optional[str]]:
    """
    Returns (device_id, dataset_key_or_none).
    """
    settings = get_settings()
    expected_key = dataset_service.construct_expected_s3_key(device_id, start_time, end_time)
    if await s3_client.object_exists(expected_key):
        return device_id, expected_key

    # Reuse the same "best available dataset" strategy as single-device execution.
    # This prevents strict fleet mode from failing when exact range export is absent
    # but valid historical data exists for the device.
    fallback_key = await dataset_service.get_best_available_dataset_key(
        device_id=device_id,
        start_time=start_time,
        end_time=end_time,
    )
    if fallback_key:
        return device_id, fallback_key

    if settings.ml_data_readiness_gate_enabled:
        try:
            await _trigger_export(device_id)
            exists = await _wait_for_dataset_key(s3_client, expected_key)
            if exists:
                return device_id, expected_key
            fallback_after_export = await dataset_service.get_best_available_dataset_key(
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
            )
            if fallback_after_export:
                return device_id, fallback_after_export
        except Exception:
            fallback_after_error = await dataset_service.get_best_available_dataset_key(
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
            )
            if fallback_after_error:
                return device_id, fallback_after_error
            return device_id, None

    return device_id, None


async def _update_parent_progress(parent_job_id: str, progress: float, message: str) -> None:
    async with async_session_maker() as session:
        repo = MySQLResultRepository(session)
        await repo.update_job_progress(parent_job_id, progress=progress, message=message)


async def _fail_parent_job(parent_job_id: str, message: str, details: Dict[str, object]) -> None:
    async with async_session_maker() as session:
        repo = MySQLResultRepository(session)
        await repo.save_results(
            job_id=parent_job_id,
            results={
                "formatted": {
                    "analysis_type": "fleet",
                    "job_id": parent_job_id,
                    "fleet_health_score": 0.0,
                    "worst_device_id": None,
                    "worst_device_health": 0.0,
                    "critical_devices": [],
                    "source_analysis_type": details.get("analysis_type", "prediction"),
                    "device_summaries": [],
                    "execution_metadata": {
                        "data_readiness": "not_ready",
                        "devices_failed": details.get("devices_failed", []),
                        "reason": message,
                    },
                }
            },
            accuracy_metrics={},
            execution_time_seconds=0,
        )
        await repo.update_job_status(
            job_id=parent_job_id,
            status=JobStatus.FAILED,
            completed_at=datetime.now(timezone.utc),
            message=message,
            error_message=message,
        )


async def _monitor_fleet(
    parent_job_id: str,
    child_jobs: Dict[str, str],
    analysis_type: str,
) -> None:
    formatter = ResultFormatter()
    while True:
        await asyncio.sleep(2)
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session)
            completed: List[dict] = []
            failed: List[dict] = []
            running_count = 0

            for device_id, child_id in child_jobs.items():
                job = await repo.get_job(child_id)
                if job.status == JobStatus.COMPLETED.value:
                    completed.append({"device_id": device_id, "job_id": child_id, "results": job.results or {}})
                elif job.status == JobStatus.FAILED.value:
                    failed.append(
                        {
                            "device_id": device_id,
                            "job_id": child_id,
                            "message": job.error_message or job.message or "Job failed",
                        }
                    )
                else:
                    running_count += 1

            total = len(child_jobs)
            done = len(completed)
            progress = 35.0 + (done / max(1, total)) * 60.0
            await repo.update_job_progress(
                parent_job_id,
                progress=min(95.0, progress),
                message=f"Running analytics for fleet ({done}/{total} completed)",
            )

            if failed:
                devices_failed = [f"{f['device_id']}: {f['message']}" for f in failed]
                await repo.save_results(
                    job_id=parent_job_id,
                    results={
                        "children": child_jobs,
                        "failed_children": failed,
                        "formatted": {
                            "analysis_type": "fleet",
                            "job_id": parent_job_id,
                            "fleet_health_score": 0.0,
                            "worst_device_id": None,
                            "worst_device_health": 0.0,
                            "critical_devices": [],
                            "source_analysis_type": analysis_type,
                            "device_summaries": [],
                            "execution_metadata": {
                                "fleet_policy": "strict",
                                "devices_failed": devices_failed,
                                "reason": "One or more device jobs failed",
                            },
                        },
                    },
                    accuracy_metrics={},
                    execution_time_seconds=0,
                )
                await repo.update_job_status(
                    parent_job_id,
                    status=JobStatus.FAILED,
                    completed_at=datetime.now(timezone.utc),
                    progress=100.0,
                    message="Fleet analysis failed - strict mode requires all devices",
                    error_message="; ".join(devices_failed),
                )
                return

            if running_count == 0 and done == total:
                device_formatted = []
                for item in completed:
                    formatted = (item["results"] or {}).get("formatted")
                    if formatted:
                        device_formatted.append(formatted)
                fleet_formatted = formatter.format_fleet_results(
                    job_id=parent_job_id,
                    analysis_type=analysis_type,
                    device_results=device_formatted,
                    child_job_map=child_jobs,
                )
                fleet_formatted["execution_metadata"] = {
                    "fleet_policy": "strict",
                    "children_count": total,
                    "devices_failed": [],
                }
                await repo.save_results(
                    job_id=parent_job_id,
                    results={
                        "children": child_jobs,
                        "formatted": fleet_formatted,
                    },
                    accuracy_metrics={},
                    execution_time_seconds=0,
                )
                await repo.update_job_status(
                    parent_job_id,
                    status=JobStatus.COMPLETED,
                    completed_at=datetime.now(timezone.utc),
                    progress=100.0,
                    message="Fleet analysis completed successfully",
                )
                return


async def _run_fleet_job(parent_job_id: str, req: FleetAnalyticsRequest, app) -> None:
    try:
        settings = get_settings()
        s3_client = S3Client()
        dataset_service = DatasetService(s3_client)
        device_ids = req.device_ids or await _fetch_all_device_ids()
        if not device_ids:
            await _fail_parent_job(
                parent_job_id,
                "No devices available for fleet analysis",
                {"analysis_type": req.analysis_type, "devices_failed": []},
            )
            return

        await _update_parent_progress(parent_job_id, 10.0, f"Checking data readiness for {len(device_ids)} devices")

        checks = await asyncio.gather(
            *[
                _ensure_device_ready(
                    s3_client=s3_client,
                    dataset_service=dataset_service,
                    device_id=device_id,
                    start_time=req.start_time,
                    end_time=req.end_time,
                )
                for device_id in device_ids
            ]
        )
        ready_keys: Dict[str, str] = {device_id: key for device_id, key in checks if key}
        failed_devices: List[str] = [device_id for device_id, key in checks if not key]

        if failed_devices and settings.ml_fleet_strict_enabled:
            await _fail_parent_job(
                parent_job_id,
                "Data not ready for one or more devices. Please start the device and ensure telemetry is flowing, then retry.",
                {
                    "analysis_type": req.analysis_type,
                    "devices_failed": failed_devices,
                },
            )
            return

        await _update_parent_progress(parent_job_id, 30.0, "Submitting device analytics jobs")
        child_jobs: Dict[str, str] = {}
        model_name = req.model_name or _default_model_for(req.analysis_type)
        for device_id, dataset_key in ready_keys.items():
            child_id = str(uuid4())
            child_request = AnalyticsRequest(
                device_id=device_id,
                dataset_key=dataset_key,
                analysis_type=AnalyticsType(req.analysis_type),
                model_name=model_name,
                parameters=req.parameters or {},
            )
            async with async_session_maker() as session:
                repo = MySQLResultRepository(session)
                await repo.create_job(
                    job_id=child_id,
                    device_id=device_id,
                    analysis_type=req.analysis_type,
                    model_name=model_name,
                    date_range_start=req.start_time,
                    date_range_end=req.end_time,
                    parameters=req.parameters or {},
                )
            await app.state.job_queue.submit_job(job_id=child_id, request=child_request)
            child_jobs[device_id] = child_id

        if not child_jobs:
            await _fail_parent_job(
                parent_job_id,
                "No devices have usable data for the selected range. Please start the device and ensure telemetry is flowing, then retry.",
                {"analysis_type": req.analysis_type, "devices_failed": failed_devices},
            )
            return

        await _monitor_fleet(
            parent_job_id=parent_job_id,
            child_jobs=child_jobs,
            analysis_type=req.analysis_type,
        )
    except Exception as exc:
        await _fail_parent_job(
            parent_job_id,
            f"Fleet orchestration failed: {exc}",
            {"analysis_type": req.analysis_type, "devices_failed": []},
        )


@router.post(
    "/run-fleet",
    response_model=AnalyticsJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_fleet_analytics(
    request: FleetAnalyticsRequest,
    app_request: Request,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> AnalyticsJobResponse:
    """
    Submit strict fleet analytics as a parent job.
    Parent status fails if any child device fails.
    """
    parent_job_id = str(uuid4())

    await result_repo.create_job(
        job_id=parent_job_id,
        device_id="ALL",
        analysis_type=request.analysis_type,
        model_name=request.model_name or _default_model_for(request.analysis_type),
        date_range_start=request.start_time,
        date_range_end=request.end_time,
        parameters={
            "fleet_mode": "strict",
            "device_ids": request.device_ids,
            **(request.parameters or {}),
        },
    )
    await result_repo.update_job_status(
        job_id=parent_job_id,
        status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        progress=1.0,
        message="Fleet job accepted",
    )

    task = asyncio.create_task(_run_fleet_job(parent_job_id, request, app_request.app))
    if not hasattr(app_request.app.state, "fleet_tasks"):
        app_request.app.state.fleet_tasks = set()
    app_request.app.state.fleet_tasks.add(task)
    task.add_done_callback(app_request.app.state.fleet_tasks.discard)

    return AnalyticsJobResponse(
        job_id=parent_job_id,
        status=JobStatus.RUNNING,
        message="Fleet job started",
    )


@router.get(
    "/status/{job_id}",
    response_model=JobStatusResponse,
)
async def get_job_status(
    job_id: str,
    app_request: Request,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> JobStatusResponse:
    """Get the current status of an analytics job."""
    try:
        job = await result_repo.get_job(job_id)
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus(job.status),
            progress=job.progress,
            message=job.message,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
    except JobNotFoundError:
        pending_jobs = getattr(app_request.app.state, "pending_jobs", {})
        pending = pending_jobs.get(job_id)
        if pending:
            return JobStatusResponse(
                job_id=job_id,
                status=JobStatus.PENDING,
                progress=0,
                message=pending.get("message") or "Job queued successfully",
                created_at=pending.get("created_at"),
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )


@router.get(
    "/results/{job_id}",
    response_model=AnalyticsResultsResponse,
)
async def get_analytics_results(
    job_id: str,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> AnalyticsResultsResponse:
    """
    Retrieve results of a completed analytics job.

    Returns model outputs, accuracy metrics, and execution details.
    """
    try:
        job = await result_repo.get_job(job_id)

        if job.status != JobStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Job {job_id} is not completed (current status: {job.status})",
            )

        return AnalyticsResultsResponse(
            job_id=job_id,
            status=JobStatus(job.status),
            device_id=job.device_id,
            analysis_type=AnalyticsType(job.analysis_type),
            model_name=job.model_name,
            date_range_start=job.date_range_start,
            date_range_end=job.date_range_end,
            results=job.results,
            accuracy_metrics=job.accuracy_metrics,
            execution_time_seconds=job.execution_time_seconds,
            completed_at=job.completed_at,
        )
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )


# ------------------------------------------------------------------
# ✅ PERMANENT FIX – advertise only runnable models
# ------------------------------------------------------------------
@router.get(
    "/models",
    response_model=SupportedModelsResponse,
)
async def get_supported_models() -> SupportedModelsResponse:
    """Get list of supported analytics models by type."""

    forecasting_models = ["prophet"]

    # Only expose ARIMA if statsmodels is actually installed
    try:
        import statsmodels  # noqa: F401
        forecasting_models.append("arima")
    except Exception:
        pass

    return SupportedModelsResponse(
        anomaly_detection=[
            "isolation_forest",
            "autoencoder",
        ],
        failure_prediction=[
            "random_forest",
            "gradient_boosting",
        ],
        forecasting=forecasting_models,
    )


@router.get(
    "/jobs",
    response_model=List[JobStatusResponse],
)
async def list_jobs(
    status: Optional[JobStatus] = None,
    device_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> List[JobStatusResponse]:
    """List analytics jobs with optional filtering."""
    jobs = await result_repo.list_jobs(
        status=status.value if status else None,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )

    return [
        JobStatusResponse(
            job_id=job.job_id,
            status=JobStatus(job.status),
            progress=job.progress,
            message=job.message,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
        for job in jobs
    ]


# ------------------------------------------------------------------
# ✅ STEP-1 – Dataset listing endpoint
# ------------------------------------------------------------------

@router.get("/datasets")
async def list_datasets(
    device_id: str = Query(..., description="Device ID"),
):
    """
    List available exported datasets for a device.

    This reads directly from S3/MinIO and returns available dataset objects.
    """

    s3_client = S3Client()
    dataset_service = DatasetService(s3_client)

    datasets = await dataset_service.list_available_datasets(
        device_id=device_id
    )

    return {
        "device_id": device_id,
        "datasets": datasets,
    }


@router.get("/retrain-status")
async def get_retrain_status(request: Request) -> dict:
    """Returns the last auto-retrain status per device."""
    retrainer = getattr(request.app.state, "retrainer", None)
    if not retrainer:
        return {}
    return retrainer.get_status()


@router.get("/formatted-results/{job_id}")
async def get_formatted_results(
    job_id: str,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> dict:
    """
    Returns dashboard-ready structured results for a completed job.
    """
    try:
        job = await result_repo.get_job(job_id)
        if job.status != JobStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Job {job_id} is not completed (status: {job.status})",
            )
        formatted = (job.results or {}).get("formatted")
        if not formatted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Formatted results not available for this job",
            )
        return formatted
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
