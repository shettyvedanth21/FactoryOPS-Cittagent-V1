"""Telemetry processing service."""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from src.config import settings
from src.models import TelemetryPayload
from src.repositories import DLQRepository, InfluxDBRepository
from src.services.enrichment_service import EnrichmentService
from src.services.rule_engine_client import RuleEngineClient
from src.utils import (
    get_logger,
    log_telemetry_error,
    log_telemetry_processed,
    TelemetryValidator,
)

logger = get_logger(__name__)


class TelemetryServiceError(Exception):
    """Raised when telemetry processing fails."""
    pass


class TelemetryService:
    """
    Main service for processing telemetry data.

    Orchestrates:
    - Validation
    - Metadata enrichment
    - Rule engine calls
    - InfluxDB persistence
    - WebSocket broadcasting
    - DLQ handling for failures
    """

    def __init__(
        self,
        influx_repository: Optional[InfluxDBRepository] = None,
        dlq_repository: Optional[DLQRepository] = None,
        enrichment_service: Optional[EnrichmentService] = None,
        rule_engine_client: Optional[RuleEngineClient] = None,
    ):
        self.influx_repository = influx_repository or InfluxDBRepository()
        self.dlq_repository = dlq_repository or DLQRepository()
        self.enrichment_service = enrichment_service or EnrichmentService()
        self.rule_engine_client = rule_engine_client or RuleEngineClient()

        self._processing_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._device_sync_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(
            maxsize=max(1, settings.device_sync_queue_maxsize)
        )
        self._device_sync_workers: list[asyncio.Task] = []
        self._device_sync_client: Optional[httpx.AsyncClient] = None

        logger.info("TelemetryService initialized")

    async def start(self) -> None:
        """Start background worker for async processing."""
        self._worker_task = asyncio.create_task(self._processing_worker())
        if settings.device_sync_enabled:
            self._device_sync_client = httpx.AsyncClient(timeout=settings.device_service_timeout)
            worker_count = max(1, settings.device_sync_workers)
            self._device_sync_workers = [
                asyncio.create_task(self._device_sync_worker(index))
                for index in range(worker_count)
            ]
        logger.info("TelemetryService background worker started")

    async def stop(self) -> None:
        """Stop background worker."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        for worker in self._device_sync_workers:
            worker.cancel()
        if self._device_sync_workers:
            await asyncio.gather(*self._device_sync_workers, return_exceptions=True)
        self._device_sync_workers = []
        if self._device_sync_client:
            await self._device_sync_client.aclose()
            self._device_sync_client = None
        logger.info("TelemetryService background worker stopped")

    async def process_telemetry_message(
        self,
        raw_payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> None:
        """Process incoming telemetry message."""
        correlation_id = correlation_id or str(uuid.uuid4())

        try:
            is_valid, error_type, error_message = TelemetryValidator.validate_payload(
                raw_payload
            )

            if not is_valid:
                self.dlq_repository.send(
                    original_payload=raw_payload,
                    error_type=error_type or "validation_error",
                    error_message=error_message or "Validation failed",
                )
                log_telemetry_error(
                    logger=logger,
                    device_id=raw_payload.get("device_id", "unknown"),
                    correlation_id=correlation_id,
                    error_type=error_type or "validation_error",
                    error_message=error_message or "Validation failed",
                    payload=raw_payload,
                )
                return

            try:
                payload = TelemetryPayload(**raw_payload)
            except Exception as e:
                self.dlq_repository.send(
                    original_payload=raw_payload,
                    error_type="parse_error",
                    error_message=str(e),
                )
                log_telemetry_error(
                    logger=logger,
                    device_id=raw_payload.get("device_id", "unknown"),
                    correlation_id=correlation_id,
                    error_type="parse_error",
                    error_message=str(e),
                    payload=raw_payload,
                )
                return

            await self._processing_queue.put(
                {
                    "payload": payload,
                    "correlation_id": correlation_id,
                    "raw_payload": raw_payload,
                }
            )

            logger.debug(
                "Telemetry queued for processing",
                device_id=payload.device_id,
                correlation_id=correlation_id,
            )

        except Exception as e:
            logger.error(
                "Unexpected error processing telemetry message",
                error=str(e),
                correlation_id=correlation_id,
            )
            self.dlq_repository.send(
                original_payload=raw_payload,
                error_type="unexpected_error",
                error_message=str(e),
            )

    async def _processing_worker(self) -> None:
        """Background worker to process queued telemetry."""
        logger.info("Processing worker started")

        while True:
            try:
                item = await self._processing_queue.get()

                try:
                    await self._process_telemetry_async(
                        payload=item["payload"],
                        correlation_id=item["correlation_id"],
                        raw_payload=item["raw_payload"],
                    )
                except Exception as e:
                    logger.error(
                        "Error in processing worker",
                        error=str(e),
                    )
                finally:
                    self._processing_queue.task_done()

            except asyncio.CancelledError:
                logger.info("Processing worker cancelled")
                break
            except Exception as e:
                logger.error(
                    "Unexpected error in processing worker",
                    error=str(e),
                )

    async def _process_telemetry_async(
        self,
        payload: TelemetryPayload,
        correlation_id: str,
        raw_payload: Dict[str, Any],
    ) -> None:
        """Process telemetry asynchronously."""
        try:
            payload = await self.enrichment_service.enrich_telemetry(payload)

            write_success = self.influx_repository.write_telemetry(payload)

            if not write_success:
                self.dlq_repository.send(
                    original_payload=raw_payload,
                    error_type="influxdb_write_error",
                    error_message="Failed to write to InfluxDB",
                )
                log_telemetry_error(
                    logger=logger,
                    device_id=payload.device_id,
                    correlation_id=correlation_id,
                    error_type="influxdb_write_error",
                    error_message="Failed to write to InfluxDB",
                    payload=raw_payload,
                )
                return

            asyncio.create_task(self.rule_engine_client.evaluate_rules(payload))

            dynamic_fields = payload.get_dynamic_fields()
            
            try:
                from src.api.websocket import broadcast_telemetry
                await broadcast_telemetry(
                    device_id=payload.device_id,
                    telemetry_data=dynamic_fields,
                )
            except Exception as e:
                logger.warning(
                    "Failed to broadcast telemetry via WebSocket",
                    device_id=payload.device_id,
                    error=str(e),
                )

            await self._enqueue_device_sync(
                payload=payload.model_dump(mode="json"),
                device_id=payload.device_id,
                dynamic_fields=dynamic_fields,
                correlation_id=correlation_id,
            )

            log_telemetry_processed(
                logger=logger,
                device_id=payload.device_id,
                correlation_id=correlation_id,
                enrichment_status=payload.enrichment_status.value,
            )

        except Exception as e:
            logger.error(
                "Error in async processing",
                device_id=payload.device_id,
                correlation_id=correlation_id,
                error=str(e),
            )
            self.dlq_repository.send(
                original_payload=raw_payload,
                error_type="processing_error",
                error_message=str(e),
            )
            log_telemetry_error(
                logger=logger,
                device_id=payload.device_id,
                correlation_id=correlation_id,
                error_type="processing_error",
                error_message=str(e),
                payload=raw_payload,
            )

    async def _enqueue_device_sync(
        self,
        payload: Dict[str, Any],
        device_id: str,
        dynamic_fields: Dict[str, Any],
        correlation_id: str,
    ) -> None:
        if not settings.device_sync_enabled:
            return
        task = {
            "payload": payload,
            "device_id": device_id,
            "dynamic_fields": dynamic_fields,
            "correlation_id": correlation_id,
        }
        try:
            self._device_sync_queue.put_nowait(task)
        except asyncio.QueueFull:
            logger.error(
                "Device sync queue full; task dropped to DLQ",
                device_id=device_id,
                queue_maxsize=settings.device_sync_queue_maxsize,
                correlation_id=correlation_id,
            )
            self.dlq_repository.send(
                original_payload=payload,
                error_type="device_sync_queue_full",
                error_message="Device sync queue is full; task dropped",
            )

    async def _device_sync_worker(self, worker_index: int) -> None:
        logger.info("Device sync worker started", worker_index=worker_index)
        while True:
            try:
                task = await self._device_sync_queue.get()
                try:
                    await self._run_device_sync_task(task=task)
                finally:
                    self._device_sync_queue.task_done()
            except asyncio.CancelledError:
                logger.info("Device sync worker stopped", worker_index=worker_index)
                return
            except Exception as exc:
                logger.error("Unexpected error in device sync worker", worker_index=worker_index, error=str(exc))

    async def _run_device_sync_task(self, task: Dict[str, Any]) -> None:
        if self._device_sync_client is None:
            return
        payload = task["payload"]
        device_id = task["device_id"]
        dynamic_fields = task["dynamic_fields"]
        correlation_id = task["correlation_id"]
        max_retries = max(1, settings.device_sync_max_retries)
        base_backoff = max(0.1, settings.device_sync_retry_backoff_sec)
        max_backoff = max(base_backoff, settings.device_sync_retry_backoff_max_sec)

        for attempt in range(1, max_retries + 1):
            try:
                await self._sync_device_state(
                    device_id=device_id,
                    dynamic_fields=dynamic_fields,
                )
                return
            except Exception as exc:
                if attempt >= max_retries:
                    logger.error(
                        "Device sync failed after retries",
                        device_id=device_id,
                        correlation_id=correlation_id,
                        attempts=attempt,
                        error=str(exc),
                    )
                    self.dlq_repository.send(
                        original_payload=payload,
                        error_type="device_sync_error",
                        error_message=str(exc),
                        retry_count=attempt,
                    )
                    return
                backoff = min(base_backoff * (2 ** (attempt - 1)), max_backoff)
                logger.warning(
                    "Device sync attempt failed; retrying",
                    device_id=device_id,
                    correlation_id=correlation_id,
                    attempt=attempt,
                    backoff_seconds=backoff,
                    error=str(exc),
                )
                await asyncio.sleep(backoff)

    async def _sync_device_state(self, device_id: str, dynamic_fields: Dict[str, Any]) -> None:
        if self._device_sync_client is None:
            return
        device_service_url = settings.device_service_url or "http://device-service:8000"
        heartbeat_response = await self._device_sync_client.post(
            f"{device_service_url}/api/v1/devices/{device_id}/heartbeat"
        )
        heartbeat_response.raise_for_status()
        sync_response = await self._device_sync_client.post(
            f"{device_service_url}/api/v1/devices/{device_id}/properties/sync",
            json=dynamic_fields,
        )
        sync_response.raise_for_status()

    async def query_telemetry(
        self,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ):
        return self.influx_repository.query_telemetry(
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    async def get_telemetry(
        self,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        fields: Optional[list[str]] = None,
        aggregate: Optional[str] = None,
        interval: Optional[str] = None,
        limit: int = 1000,
    ) -> list:
        return self.influx_repository.query_telemetry(
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
            fields=fields,
            aggregate=aggregate,
            interval=interval,
            limit=limit,
        )

    async def get_stats(
        self,
        device_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Optional[Any]:
        return self.influx_repository.get_stats(
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
        )

    async def close(self) -> None:
        """Close all service connections."""
        await self.stop()
        try:
            logger.info("DLQ operational stats", **self.dlq_repository.get_operational_stats())
        except Exception as exc:
            logger.warning("Failed to fetch DLQ operational stats", error=str(exc))
        self.influx_repository.close()
        self.dlq_repository.close()
        await self.enrichment_service.close()
        await self.rule_engine_client.close()
        logger.info("TelemetryService closed")
