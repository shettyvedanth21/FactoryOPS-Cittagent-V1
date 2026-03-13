"""Job worker for processing analytics jobs."""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
import structlog
import socket

from src.config.settings import get_settings
from src.infrastructure.database import async_session_maker
from src.infrastructure.mysql_repository import MySQLResultRepository
from src.infrastructure.s3_client import S3Client
from src.models.database import WorkerHeartbeat
from src.services.dataset_service import DatasetService
from src.services.job_runner import JobRunner
from src.models.schemas import AnalyticsRequest, JobStatus
from src.utils.exceptions import AnalyticsError, DatasetNotFoundError
from src.workers.job_queue import Job, QueueBackend

logger = structlog.get_logger()


class JobWorker:
    """Worker that processes analytics jobs from the queue."""

    def __init__(
        self,
        job_queue: QueueBackend,
        max_concurrent: int = 3,
    ):
        settings = get_settings()
        self._queue = job_queue
        self._max_concurrent = max(1, max_concurrent)
        self._running = False
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._logger = logger.bind(worker="JobWorker")
        self._current_tasks: set = set()
        self._lease_seconds = settings.job_lease_seconds
        self._heartbeat_seconds = settings.job_heartbeat_seconds
        self._max_attempts = settings.queue_max_attempts
        self._stale_scan_interval_seconds = 30
        self._worker_id = settings.redis_consumer_name or f"worker-{socket.gethostname()}"
        self._worker_heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the job worker."""
        self._running = True
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        self._logger.info(
            "worker_started",
            max_concurrent=self._max_concurrent,
        )
        self._worker_heartbeat_task = asyncio.create_task(self._worker_heartbeat_loop())
        await self._recover_stale_running_jobs()
        next_stale_scan = datetime.now(timezone.utc) + timedelta(
            seconds=self._stale_scan_interval_seconds
        )

        while self._running:
            try:
                job = await self._queue.get_job()
                if job is None:
                    now = datetime.now(timezone.utc)
                    if now >= next_stale_scan:
                        await self._recover_stale_running_jobs()
                        next_stale_scan = now + timedelta(
                            seconds=self._stale_scan_interval_seconds
                        )
                    await asyncio.sleep(0.1)
                    continue

                task = asyncio.create_task(
                    self._process_job_with_semaphore(job)
                )
                self._current_tasks.add(task)
                task.add_done_callback(self._current_tasks.discard)

            except asyncio.CancelledError:
                self._logger.info("worker_cancelled")
                break
            except Exception as e:
                self._logger.error("worker_error", error=str(e))
                await asyncio.sleep(1)

    async def _recover_stale_running_jobs(self) -> None:
        """Requeue jobs left in running state when worker lease is stale/missing."""
        now = datetime.now(timezone.utc)
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session)
            running_jobs = await repo.list_jobs(
                status=JobStatus.RUNNING.value, limit=5000, offset=0
            )

            for job in running_jobs:
                lease = getattr(job, "worker_lease_expires_at", None)
                if lease is not None and lease.tzinfo is None:
                    lease = lease.replace(tzinfo=timezone.utc)
                is_stale = lease is None or lease <= now
                if not is_stale:
                    continue

                next_attempt = int(getattr(job, "attempt", 0) or 0) + 1
                if next_attempt > self._max_attempts:
                    await repo.update_job_status(
                        job_id=job.job_id,
                        status=JobStatus.FAILED,
                        completed_at=now,
                        message="Job failed after stale worker recovery attempts exhausted",
                        error_message="STALE_WORKER_LEASE",
                    )
                    await repo.update_job_queue_metadata(
                        job_id=job.job_id,
                        error_code="STALE_WORKER_LEASE",
                        worker_lease_expires_at=None,
                    )
                    self._logger.error(
                        "stale_running_job_failed",
                        job_id=job.job_id,
                        attempt=next_attempt,
                    )
                    continue

                request = AnalyticsRequest(
                    device_id=job.device_id,
                    analysis_type=job.analysis_type,
                    model_name=job.model_name,
                    start_time=job.date_range_start,
                    end_time=job.date_range_end,
                    parameters=job.parameters or {},
                )
                await self._queue.submit_job(
                    job_id=job.job_id, request=request, attempt=next_attempt
                )
                await repo.update_job_status(
                    job_id=job.job_id,
                    status=JobStatus.PENDING,
                    progress=0.0,
                    message=f"Requeued after stale worker lease (attempt {next_attempt}/{self._max_attempts})",
                    error_message=None,
                )
                await repo.update_job_queue_metadata(
                    job_id=job.job_id,
                    attempt=next_attempt,
                    queue_enqueued_at=now,
                    worker_lease_expires_at=None,
                    error_code="STALE_WORKER_LEASE",
                )
                self._logger.warning(
                    "stale_running_job_requeued",
                    job_id=job.job_id,
                    attempt=next_attempt,
                )

    async def _process_job_with_semaphore(self, job: Job) -> None:
        if self._semaphore:
            async with self._semaphore:
                await self._process_job(job)

    # ---------------------------------------------------------
    # NEW: extract dates from dataset key
    # ---------------------------------------------------------
    def _extract_dates_from_dataset_key(self, dataset_key: str):
        """
        Expected:
          datasets/D1/20260210_20260210.parquet
        """

        m = re.search(r"(\d{8})_(\d{8})", dataset_key)
        if not m:
            raise AnalyticsError(
                f"Invalid dataset key format: {dataset_key}"
            )

        start = datetime.strptime(m.group(1), "%Y%m%d")
        end = datetime.strptime(m.group(2), "%Y%m%d")

        return start, end

    async def _process_job(self, job: Job) -> None:
        job_id = job.job_id
        request = job.request

        self._logger.info(
            "processing_job",
            job_id=job_id,
            analysis_type=request.analysis_type.value,
        )

        async with async_session_maker() as session:
            try:
                result_repo = MySQLResultRepository(session)

                # ---------------------------------------------------------
                # PERMANENT FIX:
                # Fill date_range_* when dataset_key is used
                # ---------------------------------------------------------
                if request.dataset_key:
                    date_range_start, date_range_end = (
                        self._extract_dates_from_dataset_key(
                            request.dataset_key
                        )
                    )
                else:
                    date_range_start = request.start_time
                    date_range_end = request.end_time

                await result_repo.update_job_queue_metadata(
                    job_id=job_id,
                    attempt=job.attempt,
                    queue_started_at=datetime.now(timezone.utc),
                    worker_lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._lease_seconds),
                    last_heartbeat_at=datetime.now(timezone.utc),
                )

                s3_client = S3Client()
                dataset_service = DatasetService(s3_client)

                runner = JobRunner(dataset_service, result_repo)
                heartbeat = asyncio.create_task(self._heartbeat_loop(job_id))
                try:
                    await runner.run_job(job_id, request)
                finally:
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except asyncio.CancelledError:
                        pass
                    await result_repo.update_job_queue_metadata(
                        job_id=job_id,
                        worker_lease_expires_at=None,
                    )

                self._logger.info("job_completed", job_id=job_id)
                if job.receipt:
                    await self._queue.ack_job(job.receipt)

            except DatasetNotFoundError as e:
                self._logger.error(
                    "job_failed_dataset_not_found",
                    job_id=job_id,
                    error=str(e),
                )
                await self._retry_or_fail(job, "DATASET_NOT_FOUND", str(e))

            except AnalyticsError as e:
                self._logger.error(
                    "job_failed_analytics_error",
                    job_id=job_id,
                    error=str(e),
                )
                await self._retry_or_fail(job, "ANALYTICS_ERROR", str(e))

            except Exception as e:
                self._logger.error(
                    "job_failed_unexpected",
                    job_id=job_id,
                    error=str(e),
                    exc_info=True,
                )
                await self._retry_or_fail(job, "UNEXPECTED_ERROR", f"Unexpected error: {e}")

            finally:
                self._queue.task_done()

    async def _heartbeat_loop(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            async with async_session_maker() as session:
                result_repo = MySQLResultRepository(session)
                now = datetime.now(timezone.utc)
                await result_repo.update_job_queue_metadata(
                    job_id=job_id,
                    last_heartbeat_at=now,
                    worker_lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                )

    async def _retry_or_fail(self, job: Job, error_code: str, error_message: str) -> None:
        if job.attempt < self._max_attempts:
            backoff = min(30, 2 ** (job.attempt - 1))
            await asyncio.sleep(backoff)
            if job.receipt:
                await self._queue.ack_job(job.receipt)
            await self._queue.submit_job(job.job_id, job.request, attempt=job.attempt + 1)
            async with async_session_maker() as session:
                repo = MySQLResultRepository(session)
                await repo.update_job_status(
                    job_id=job.job_id,
                    status=JobStatus.PENDING,
                    progress=0.0,
                    message=f"Retrying job (attempt {job.attempt + 1}/{self._max_attempts})",
                    error_message=None,
                )
                await repo.update_job_queue_metadata(
                    job_id=job.job_id,
                    attempt=job.attempt + 1,
                    error_code=error_code,
                    queue_enqueued_at=datetime.now(timezone.utc),
                    worker_lease_expires_at=None,
                )
            return

        await self._mark_job_failed(job.job_id, error_message, error_code=error_code)
        await self._queue.dead_letter(job, error_message)

    async def _mark_job_failed(self, job_id: str, error_message: str, error_code: Optional[str] = None) -> None:
        try:
            async with async_session_maker() as session:
                result_repo = MySQLResultRepository(session)
                msg = "Job failed"
                lower = (error_message or "").lower()
                if "dataset not found" in lower or "no such key" in lower:
                    msg = (
                        "No dataset found for selected date range. "
                        "Please start the device and ensure telemetry is flowing, then retry analysis."
                    )
                elif "no numeric columns" in lower or "insufficient" in lower:
                    msg = "Insufficient signal/data for reliable analytics. Please collect more telemetry."

                await result_repo.update_job_status(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    completed_at=datetime.utcnow(),
                    message=msg,
                    error_message=error_message,
                )
                await result_repo.update_job_queue_metadata(
                    job_id=job_id,
                    error_code=error_code,
                    worker_lease_expires_at=None,
                )
        except Exception as e:
            self._logger.error(
                "failed_to_mark_job_failed",
                job_id=job_id,
                error=str(e),
            )

    async def stop(self) -> None:
        self._logger.info("stopping_worker")
        self._running = False
        if self._worker_heartbeat_task:
            self._worker_heartbeat_task.cancel()
            try:
                await self._worker_heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._current_tasks:
            self._logger.info(
                "waiting_for_tasks",
                task_count=len(self._current_tasks),
            )
            await asyncio.gather(*self._current_tasks, return_exceptions=True)

        self._logger.info("worker_stopped")

    async def _worker_heartbeat_loop(self) -> None:
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                async with async_session_maker() as session:
                    row = await session.get(WorkerHeartbeat, self._worker_id)
                    if row is None:
                        row = WorkerHeartbeat(
                            worker_id=self._worker_id,
                            app_role="worker",
                            status="alive",
                            last_heartbeat_at=now,
                        )
                        session.add(row)
                    else:
                        row.status = "alive"
                        row.last_heartbeat_at = now
                    await session.commit()
            except Exception as exc:
                self._logger.warning("worker_heartbeat_failed", error=str(exc))
            await asyncio.sleep(max(5, self._heartbeat_seconds))
