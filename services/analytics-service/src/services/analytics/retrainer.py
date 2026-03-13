"""Weekly retrainer that submits jobs through existing queue."""

import asyncio
import os
from datetime import datetime
from typing import List, Optional

import structlog

logger = structlog.get_logger()

DEVICE_SERVICE_URL = os.getenv(
    "DEVICE_SERVICE_URL",
    "http://device-service:8000/api/v1/devices",
)


class WeeklyRetrainer:
    def __init__(self, job_queue, dataset_service):
        self._job_queue = job_queue
        self._dataset_service = dataset_service
        self._status: dict = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_device_ids: List[str] = []

    async def start(self, device_ids: List[str]) -> None:
        self._running = True
        self._last_device_ids = device_ids
        self._task = asyncio.create_task(self._loop())
        logger.info("weekly_retrainer_started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(300)
        while self._running:
            device_ids = await self._fetch_device_ids()
            await self._retrain_all(device_ids)
            await asyncio.sleep(7 * 24 * 3600)

    async def _fetch_device_ids(self) -> List[str]:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    DEVICE_SERVICE_URL,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        devices = (
                            data
                            if isinstance(data, list)
                            else data.get("devices", data.get("data", []))
                        )
                        ids = [
                            d.get("id") or d.get("device_id")
                            for d in devices
                            if d.get("id") or d.get("device_id")
                        ]
                        if ids:
                            self._last_device_ids = ids
                            logger.info("retrainer_fetched_devices", count=len(ids))
                        return ids
        except Exception as e:
            logger.warning(
                "retrainer_device_fetch_failed",
                error=str(e),
                fallback_count=len(self._last_device_ids),
            )
        return self._last_device_ids

    async def _retrain_all(self, device_ids: List[str]) -> None:
        if not device_ids:
            logger.warning("retrainer_no_devices_found")
            return

        for device_id in device_ids:
            await self._retrain_device(device_id)

    async def _retrain_device(self, device_id: str) -> None:
        try:
            from uuid import uuid4

            from src.infrastructure.database import async_session_maker
            from src.infrastructure.mysql_repository import MySQLResultRepository
            from src.models.schemas import AnalyticsRequest, AnalyticsType

            datasets = await self._dataset_service.list_available_datasets(device_id)
            if not datasets:
                logger.warning("retrain_skipped_no_data", device_id=device_id)
                self._status[device_id] = {
                    "status": "skipped",
                    "reason": "no_datasets",
                    "timestamp": datetime.utcnow().isoformat(),
                }
                return

            request = AnalyticsRequest(
                device_id=device_id,
                analysis_type=AnalyticsType.ANOMALY,
                model_name="isolation_forest",
                parameters={"sensitivity": "medium", "lookback_days": 30},
                dataset_key=datasets[0].get("key") if isinstance(datasets[0], dict) else None,
            )
            job_id = str(uuid4())
            now = datetime.utcnow()
            async with async_session_maker() as session:
                repo = MySQLResultRepository(session)
                await repo.create_job(
                    job_id=job_id,
                    device_id=device_id,
                    analysis_type=AnalyticsType.ANOMALY.value,
                    model_name="isolation_forest",
                    date_range_start=now,
                    date_range_end=now,
                    parameters=request.parameters,
                )
                await repo.update_job_queue_metadata(
                    job_id=job_id,
                    attempt=1,
                    queue_enqueued_at=now,
                )
            await self._job_queue.submit_job(job_id=job_id, request=request, attempt=1)

            self._status[device_id] = {
                "status": "submitted",
                "job_id": job_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
            logger.info("retrain_job_submitted", device_id=device_id, job_id=job_id)

        except Exception as e:
            logger.error("retrain_failed", device_id=device_id, error=str(e))
            self._status[device_id] = {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }

    def get_status(self) -> dict:
        return self._status
