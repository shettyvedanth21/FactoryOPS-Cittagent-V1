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
from src.services.readiness_orchestrator import ensure_device_ready
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

    start_time = request.start_time or datetime.now(timezone.utc)
    end_time = request.end_time or start_time
    await result_repository.create_job(
        job_id=job_id,
        device_id=request.device_id,
        analysis_type=request.analysis_type.value,
        model_name=request.model_name,
        date_range_start=start_time,
        date_range_end=end_time,
        parameters=request.parameters,
    )
    await result_repository.update_job_queue_metadata(
        job_id=job_id,
        attempt=1,
        queue_enqueued_at=datetime.now(timezone.utc),
        queue_position=max(0, int(getattr(job_queue, "size", lambda: 0)() or 0)),
    )

    await job_queue.submit_job(job_id=job_id, request=request, attempt=1)
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
    skipped_devices: List[Dict[str, str]],
    total_selected: int,
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

            if running_count == 0 and (done + len(failed) == total):
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
                failed_devices = [
                    {"device_id": str(f["device_id"]), "reason": "child_job_failed", "message": str(f["message"])}
                    for f in failed
                ]
                coverage_pct = round((len(completed) / max(1, total_selected)) * 100, 1)
                fleet_formatted["execution_metadata"] = {
                    "fleet_policy": "best_effort_exact",
                    "children_count": total,
                    "devices_ready": [str(item["device_id"]) for item in completed],
                    "devices_failed": failed_devices,
                    "devices_skipped": skipped_devices,
                    "skipped_reasons": {
                        str(item.get("device_id")): str(item.get("reason"))
                        for item in skipped_devices
                    },
                    "coverage_pct": coverage_pct,
                    "selected_device_count": total_selected,
                }
                await repo.save_results(
                    job_id=parent_job_id,
                    results={
                        "children": child_jobs,
                        "failed_children": failed,
                        "skipped_children": skipped_devices,
                        "formatted": fleet_formatted,
                    },
                    accuracy_metrics={},
                    execution_time_seconds=0,
                )
                if len(completed) > 0:
                    message = f"Fleet analysis completed ({len(completed)}/{total_selected} devices analyzed)"
                    if skipped_devices or failed:
                        message += f"; skipped/failed: {len(skipped_devices) + len(failed)}"
                    final_status = JobStatus.COMPLETED
                    error_message = None
                else:
                    message = "No devices produced successful analytics results"
                    final_status = JobStatus.FAILED
                    error_message = "All fleet child jobs were skipped or failed"
                await repo.update_job_status(
                    parent_job_id,
                    status=final_status,
                    completed_at=datetime.now(timezone.utc),
                    progress=100.0,
                    message=message,
                    error_message=error_message,
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

        readiness_limit = max(1, int(settings.data_readiness_max_concurrency))
        readiness_semaphore = asyncio.Semaphore(readiness_limit)

        async def _bounded_ready_check(device_id: str):
            async with readiness_semaphore:
                return await ensure_device_ready(
                    s3_client=s3_client,
                    dataset_service=dataset_service,
                    device_id=device_id,
                    start_time=req.start_time,
                    end_time=req.end_time,
                )

        checks = await asyncio.gather(*[_bounded_ready_check(device_id) for device_id in device_ids])
        ready_keys: Dict[str, str] = {}
        skipped_devices: List[Dict[str, str]] = []
        for device_id, key, meta in checks:
            if key:
                ready_keys[str(device_id)] = str(key)
                continue
            reason = str((meta or {}).get("reason") or "dataset_not_ready")
            skipped_devices.append(
                {
                    "device_id": str(device_id),
                    "reason": reason,
                    "message": {
                        "dataset_not_ready": "Exact-range dataset is not ready yet",
                        "export_timeout": "Export timed out while preparing exact-range dataset",
                        "device_not_found": "Device not found in export pipeline",
                        "no_telemetry_in_range": "No telemetry found in selected date range",
                    }.get(reason, "Data readiness check did not pass"),
                }
            )

        logger.info(
            "fleet_data_readiness_summary",
            parent_job_id=parent_job_id,
            total_devices=len(device_ids),
            ready_devices=len(ready_keys),
            skipped_devices=len(skipped_devices),
            strict_exact_mode=bool(settings.ml_require_exact_dataset_range),
        )

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
                "No devices have exact-range datasets ready for the selected window.",
                {"analysis_type": req.analysis_type, "devices_failed": skipped_devices},
            )
            return

        await _monitor_fleet(
            parent_job_id=parent_job_id,
            child_jobs=child_jobs,
            analysis_type=req.analysis_type,
            skipped_devices=skipped_devices,
            total_selected=len(device_ids),
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
            "fleet_mode": "best_effort_exact",
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
