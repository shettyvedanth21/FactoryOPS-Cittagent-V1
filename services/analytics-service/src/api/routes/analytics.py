"""Analytics API endpoints."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import uuid4

import aiohttp
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from src.api.dependencies import get_job_queue, get_result_repository
from src.config.settings import get_settings
from src.infrastructure.database import async_session_maker
from src.infrastructure.mysql_repository import MySQLResultRepository
from src.infrastructure.s3_client import S3Client
from src.models.database import WorkerHeartbeat, FailureEventLabel, AccuracyEvaluation
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
from src.workers.job_queue import QueueBackend

from src.services.dataset_service import DatasetService
from src.services.analytics.accuracy_evaluator import AccuracyEvaluator

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
    job_queue: QueueBackend = Depends(get_job_queue),
    result_repository: ResultRepository = Depends(get_result_repository),
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

    resolved_request = request
    if not request.dataset_key and request.start_time and request.end_time:
        settings = get_settings()
        s3_client = S3Client()
        dataset_service = DatasetService(s3_client)
        _, dataset_key = await _ensure_device_ready(
            s3_client=s3_client,
            dataset_service=dataset_service,
            device_id=request.device_id,
            start_time=request.start_time,
            end_time=request.end_time,
        )
        if not dataset_key:
            if settings.ml_data_readiness_gate_enabled:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "DATASET_NOT_READY",
                        "code": "DATASET_NOT_READY",
                        "message": (
                            "No dataset available for the selected date range after export readiness checks. "
                            "Ensure telemetry exists in that range and retry."
                        ),
                        "device_id": request.device_id,
                        "start_time": request.start_time.isoformat(),
                        "end_time": request.end_time.isoformat(),
                        "data_readiness_gate_enabled": settings.ml_data_readiness_gate_enabled,
                    },
                )
            logger.warning(
                "data_readiness_soft_fail",
                device_id=request.device_id,
                start_time=request.start_time.isoformat(),
                end_time=request.end_time.isoformat(),
                message="Proceeding without resolved dataset_key because readiness gate is disabled.",
            )
        else:
            resolved_request = request.model_copy(update={"dataset_key": dataset_key})
            logger.info(
                "single_analytics_dataset_resolved",
                job_id=job_id,
                device_id=request.device_id,
                dataset_key=dataset_key,
                start_time=request.start_time.isoformat(),
                end_time=request.end_time.isoformat(),
            )

    start_time = resolved_request.start_time or datetime.now(timezone.utc)
    end_time = resolved_request.end_time or start_time
    await result_repository.create_job(
        job_id=job_id,
        device_id=resolved_request.device_id,
        analysis_type=resolved_request.analysis_type.value,
        model_name=resolved_request.model_name,
        date_range_start=start_time,
        date_range_end=end_time,
        parameters=resolved_request.parameters,
    )
    await result_repository.update_job_queue_metadata(
        job_id=job_id,
        attempt=1,
        queue_enqueued_at=datetime.now(timezone.utc),
        queue_position=max(0, int(getattr(job_queue, "size", lambda: 0)() or 0)),
    )

    await job_queue.submit_job(job_id=job_id, request=resolved_request, attempt=1)
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
        return "anomaly_ensemble"
    return "failure_ensemble"


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


async def _trigger_export(
    device_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> dict:
    settings = get_settings()
    url = f"{settings.data_export_service_url}/api/v1/exports/run"
    payload: Dict[str, object] = {"device_id": device_id}
    if start_time and end_time:
        payload["start_time"] = start_time.isoformat()
        payload["end_time"] = end_time.isoformat()

    logger.info(
        "data_readiness_trigger_export",
        device_id=device_id,
        start_time=start_time.isoformat() if start_time else None,
        end_time=end_time.isoformat() if end_time else None,
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise RuntimeError(f"export trigger failed ({resp.status}): {text}")
            return await resp.json()


async def _wait_for_dataset_key(
    device_id: str,
    data_export_service_url: str,
    s3_client: S3Client,
    expected_key: str,
) -> Optional[str]:
    settings = get_settings()
    delay = settings.data_readiness_initial_delay_seconds
    attempts = settings.data_readiness_poll_attempts
    for i in range(max(1, attempts)):
        if await s3_client.object_exists(expected_key):
            return expected_key

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{data_export_service_url}/api/v1/exports/status/{device_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        status_payload = await resp.json()
                        export_status = str(status_payload.get("status", "")).lower()
                        s3_key = status_payload.get("s3_key")

                        if export_status == "failed":
                            logger.warning(
                                "data_readiness_export_failed",
                                device_id=device_id,
                                expected_key=expected_key,
                                status_payload=status_payload,
                            )
                            return None

                        if s3_key and isinstance(s3_key, str):
                            if await s3_client.object_exists(s3_key):
                                return s3_key
        except Exception as exc:
            logger.warning(
                "data_readiness_status_check_error",
                device_id=device_id,
                expected_key=expected_key,
                error=str(exc),
            )
        await asyncio.sleep(delay * (2 ** i))
    if await s3_client.object_exists(expected_key):
        return expected_key
    return None


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
    logger.info(
        "data_readiness_check_started",
        device_id=device_id,
        expected_key=expected_key,
        start_time=start_time.isoformat(),
        end_time=end_time.isoformat(),
    )
    if await s3_client.object_exists(expected_key):
        logger.info(
            "data_readiness_expected_key_found",
            device_id=device_id,
            dataset_key=expected_key,
        )
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
        logger.info(
            "data_readiness_fallback_key_found",
            device_id=device_id,
            fallback_key=fallback_key,
            expected_key=expected_key,
        )
        return device_id, fallback_key

    if settings.ml_data_readiness_gate_enabled:
        try:
            export_trigger_response = await _trigger_export(
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
            )
            logger.info(
                "data_readiness_export_triggered",
                device_id=device_id,
                expected_key=expected_key,
                export_response=export_trigger_response,
            )
            resolved_key = await _wait_for_dataset_key(
                device_id=device_id,
                data_export_service_url=settings.data_export_service_url,
                s3_client=s3_client,
                expected_key=expected_key,
            )
            if resolved_key:
                logger.info(
                    "data_readiness_export_key_resolved",
                    device_id=device_id,
                    resolved_key=resolved_key,
                    expected_key=expected_key,
                )
                return device_id, resolved_key
            fallback_after_export = await dataset_service.get_best_available_dataset_key(
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
            )
            if fallback_after_export:
                logger.info(
                    "data_readiness_fallback_after_export",
                    device_id=device_id,
                    fallback_key=fallback_after_export,
                    expected_key=expected_key,
                )
                return device_id, fallback_after_export
        except Exception as exc:
            logger.warning(
                "data_readiness_export_error",
                device_id=device_id,
                expected_key=expected_key,
                error=str(exc),
            )
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
                await repo.update_job_queue_metadata(
                    job_id=child_id,
                    attempt=1,
                    queue_enqueued_at=datetime.now(timezone.utc),
                )
            await app.state.job_queue.submit_job(job_id=child_id, request=child_request, attempt=1)
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
            queue_position=job.queue_position,
            attempt=job.attempt,
            worker_lease_expires_at=job.worker_lease_expires_at,
            estimated_wait_seconds=max(0, int((job.queue_position or 0) * 5)),
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
            "lstm_autoencoder",
            "cusum",
        ],
        failure_prediction=[
            "xgboost",
            "lstm_classifier",
            "degradation_tracker",
        ],
        forecasting=forecasting_models,
        ensembles=[
            {
                "id": "anomaly_ensemble",
                "display_name": "Anomaly Detection — 3 Model Ensemble",
                "models": [
                    {"name": "isolation_forest", "trains": True},
                    {
                        "name": "lstm_autoencoder",
                        "trains": True,
                        "min_data": "50 sequences (~80 min)",
                    },
                    {
                        "name": "cusum",
                        "trains": False,
                        "note": "Works from minute 1",
                    },
                ],
                "voting_rule": "Alert when 2 of 3 models flag",
            },
            {
                "id": "failure_ensemble",
                "display_name": "Failure Prediction — 3 Model Ensemble",
                "models": [
                    {"name": "xgboost", "trains": True},
                    {
                        "name": "lstm_classifier",
                        "trains": True,
                        "min_data": "50 sequences (~80 min)",
                    },
                    {
                        "name": "degradation_tracker",
                        "trains": False,
                        "note": "Physics-based — no training needed",
                    },
                ],
                "voting_rule": "CRITICAL=3/3, WARNING=2/3, WATCH=1/3",
            },
        ],
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
            queue_position=job.queue_position,
            attempt=job.attempt,
            worker_lease_expires_at=job.worker_lease_expires_at,
            estimated_wait_seconds=max(0, int((job.queue_position or 0) * 5)),
        )
        for job in jobs
    ]


@router.get("/ops/queue")
async def get_queue_ops_snapshot(
    app_request: Request,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> Dict[str, object]:
    """Operational queue snapshot for SRE dashboards."""
    pending = await result_repo.list_jobs(status=JobStatus.PENDING.value, limit=5000, offset=0)
    running = await result_repo.list_jobs(status=JobStatus.RUNNING.value, limit=5000, offset=0)
    failed = await result_repo.list_jobs(status=JobStatus.FAILED.value, limit=5000, offset=0)
    settings = get_settings()
    active_workers = 0
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(10, settings.worker_heartbeat_ttl_seconds))
    async with async_session_maker() as session:
        rows = await session.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.last_heartbeat_at >= cutoff))
        active_workers = len(list(rows.scalars().all()))

    return {
        "queue_depth": len(pending),
        "consumer_lag_estimate": len(pending),
        "failed_job_count": len(failed),
        "active_workers": active_workers,
        "running_jobs": len(running),
        "queue_backend": getattr(app_request.app.state, "queue_backend", "unknown"),
    }


@router.post("/labels/failure-events")
async def ingest_failure_event_label(payload: Dict[str, object]) -> Dict[str, object]:
    """Add a maintenance/failure ground-truth label event."""
    device_id = str(payload.get("device_id") or "").strip()
    event_time_raw = payload.get("event_time")
    if not device_id or not event_time_raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="device_id and event_time are required",
        )
    try:
        event_time = datetime.fromisoformat(str(event_time_raw).replace("Z", "+00:00"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid event_time: {exc}",
        )

    row = FailureEventLabel(
        device_id=device_id,
        event_time=event_time,
        event_type=str(payload.get("event_type") or "failure"),
        severity=str(payload.get("severity") or "") or None,
        source=str(payload.get("source") or "") or "manual",
        metadata_json=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )
    async with async_session_maker() as session:
        session.add(row)
        await session.commit()

    return {"status": "accepted", "id": row.id}


@router.post("/accuracy/evaluate")
async def evaluate_accuracy(
    device_id: Optional[str] = Query(default=None),
    lookback_days: int = Query(default=90, ge=1, le=3650),
    lead_window_hours: int = Query(default=24, ge=1, le=720),
) -> Dict[str, object]:
    """Run backtest evaluation against labeled events and persist summary."""
    async with async_session_maker() as session:
        result = await AccuracyEvaluator.evaluate_failure_predictions(
            session=session,
            device_id=device_id,
            lookback_days=lookback_days,
            lead_window_hours=lead_window_hours,
        )
    return {
        "analysis_type": "prediction",
        "scope_device_id": device_id,
        **result.as_dict(),
    }


@router.get("/accuracy/latest")
async def get_latest_accuracy(device_id: Optional[str] = Query(default=None)) -> Dict[str, object]:
    """Fetch latest persisted accuracy evaluation record."""
    async with async_session_maker() as session:
        q = (
            select(AccuracyEvaluation)
            .where(AccuracyEvaluation.analysis_type == "prediction")
            .order_by(AccuracyEvaluation.created_at.desc())
            .limit(1)
        )
        if device_id:
            q = q.where(AccuracyEvaluation.scope_device_id == device_id)
        row = (await session.execute(q)).scalar_one_or_none()

    if not row:
        return {"analysis_type": "prediction", "scope_device_id": device_id, "status": "no_evaluation"}

    return {
        "analysis_type": row.analysis_type,
        "scope_device_id": row.scope_device_id,
        "sample_size": row.sample_size,
        "labeled_events": row.labeled_events,
        "precision": row.precision,
        "recall": row.recall,
        "f1_score": row.f1_score,
        "false_alert_rate": row.false_alert_rate,
        "avg_lead_hours": row.avg_lead_hours,
        "is_certified": bool(row.is_certified),
        "notes": row.notes,
        "created_at": row.created_at,
    }


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
