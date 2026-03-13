"""MySQL implementation of result repository."""

from datetime import datetime
from typing import Any, Dict, List, Optional

import math
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import AnalyticsJob, ModelArtifact
from src.models.schemas import JobStatus
from src.services.result_repository import ResultRepository, UNSET
from src.utils.exceptions import JobNotFoundError

logger = structlog.get_logger()


class MySQLResultRepository(ResultRepository):
    """MySQL implementation of result repository."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._logger = logger.bind(repository="MySQLResultRepository")

    def _sanitize_json(self, value: Any) -> Any:
        """Recursively replace NaN / inf values so JSON inserts never fail."""

        if value is None:
            return None

        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
            return value

        if hasattr(value, "tolist") and not isinstance(value, (str, bytes, bytearray)):
            try:
                return self._sanitize_json(value.tolist())
            except Exception:
                pass

        if isinstance(value, list):
            return [self._sanitize_json(v) for v in value]

        if isinstance(value, dict):
            return {k: self._sanitize_json(v) for k, v in value.items()}

        return value

    async def create_job(
        self,
        job_id: str,
        device_id: str,
        analysis_type: str,
        model_name: str,
        date_range_start: datetime,
        date_range_end: datetime,
        parameters: Optional[Dict[str, Any]],
    ) -> None:
        job = AnalyticsJob(
            job_id=job_id,
            device_id=device_id,
            analysis_type=analysis_type,
            model_name=model_name,
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            parameters=self._sanitize_json(parameters),
            status=JobStatus.PENDING.value,
            progress=0.0,
        )

        self._session.add(job)
        await self._session.commit()

        self._logger.info("job_created", job_id=job_id, device_id=device_id)

    async def get_job(self, job_id: str) -> AnalyticsJob:
        result = await self._session.execute(
            select(AnalyticsJob).where(AnalyticsJob.job_id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            raise JobNotFoundError(f"Job {job_id} not found")

        return job

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:

        job = await self.get_job(job_id)

        job.status = status.value

        if started_at:
            job.started_at = started_at
        if completed_at:
            job.completed_at = completed_at
        if progress is not None:
            job.progress = progress
        if message:
            job.message = message
        if error_message:
            job.error_message = error_message

        await self._session.commit()

        self._logger.debug(
            "job_status_updated",
            job_id=job_id,
            status=status.value,
            progress=progress,
        )

    async def update_job_progress(
        self,
        job_id: str,
        progress: float,
        message: str,
    ) -> None:

        job = await self.get_job(job_id)

        job.progress = progress
        job.message = message

        await self._session.commit()

    async def save_results(
        self,
        job_id: str,
        results: Dict[str, Any],
        accuracy_metrics: Optional[Dict[str, float]],
        execution_time_seconds: int,
    ) -> None:

        job = await self.get_job(job_id)

        job.results = self._sanitize_json(results)
        job.accuracy_metrics = self._sanitize_json(accuracy_metrics)
        job.execution_time_seconds = execution_time_seconds

        await self._session.commit()

        self._logger.info(
            "results_saved",
            job_id=job_id,
            execution_time_seconds=execution_time_seconds,
        )

    async def list_jobs(
        self,
        status: Optional[str] = None,
        device_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[AnalyticsJob]:

        query = select(AnalyticsJob).order_by(AnalyticsJob.created_at.desc())

        if status:
            query = query.where(AnalyticsJob.status == status)
        if device_id:
            query = query.where(AnalyticsJob.device_id == device_id)

        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def update_job_queue_metadata(
        self,
        job_id: str,
        attempt: Optional[int] = None,
        queue_position: Optional[int] = None,
        queue_enqueued_at: Optional[datetime] | object = UNSET,
        queue_started_at: Optional[datetime] | object = UNSET,
        worker_lease_expires_at: Optional[datetime] | object = UNSET,
        last_heartbeat_at: Optional[datetime] | object = UNSET,
        error_code: Optional[str] | object = UNSET,
    ) -> None:
        job = await self.get_job(job_id)

        if attempt is not None:
            job.attempt = int(attempt)
        if queue_position is not None:
            job.queue_position = int(queue_position)
        if queue_enqueued_at is not UNSET:
            job.queue_enqueued_at = queue_enqueued_at
        if queue_started_at is not UNSET:
            job.queue_started_at = queue_started_at
        if worker_lease_expires_at is not UNSET:
            job.worker_lease_expires_at = worker_lease_expires_at
        if last_heartbeat_at is not UNSET:
            job.last_heartbeat_at = last_heartbeat_at
        if error_code is not UNSET:
            job.error_code = error_code

        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def get_model_artifact(
        self,
        device_id: str,
        analysis_type: str,
        model_key: str,
    ) -> Optional[Dict[str, Any]]:
        result = await self._session.execute(
            select(ModelArtifact)
            .where(ModelArtifact.device_id == device_id)
            .where(ModelArtifact.analysis_type == analysis_type)
            .where(ModelArtifact.model_key == model_key)
            .order_by(ModelArtifact.updated_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            "feature_schema_hash": row.feature_schema_hash,
            "artifact_payload": row.artifact_payload,
            "model_version": row.model_version,
            "metrics": row.metrics or {},
            "updated_at": row.updated_at,
        }

    async def upsert_model_artifact(
        self,
        device_id: str,
        analysis_type: str,
        model_key: str,
        feature_schema_hash: str,
        artifact_payload: bytes,
        model_version: str = "v1",
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not artifact_payload:
            return

        existing = await self._session.execute(
            select(ModelArtifact)
            .where(ModelArtifact.device_id == device_id)
            .where(ModelArtifact.analysis_type == analysis_type)
            .where(ModelArtifact.model_key == model_key)
            .where(ModelArtifact.feature_schema_hash == feature_schema_hash)
            .limit(1)
        )
        artifact = existing.scalar_one_or_none()
        if artifact is None:
            artifact = ModelArtifact(
                device_id=device_id,
                analysis_type=analysis_type,
                model_key=model_key,
                feature_schema_hash=feature_schema_hash,
                model_version=model_version,
                artifact_payload=artifact_payload,
                metrics=self._sanitize_json(metrics),
            )
            self._session.add(artifact)
        else:
            artifact.model_version = model_version
            artifact.artifact_payload = artifact_payload
            artifact.metrics = self._sanitize_json(metrics)

        await self._session.commit()
