"""Device Service - Energy Intelligence Platform.

This module initializes the FastAPI application with all required configurations.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.config import settings
from app.database import engine
from app.logging_config import configure_logging
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for graceful startup and shutdown."""
    # Startup
    configure_logging()
    logger.info(
        "Starting Device Service",
        extra={
            "service": "device-service",
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
        }
    )
    
    # Create tables if they don't exist
    from app.database import Base
    from app.models.device import (
        Device,
        DeviceShift,
        ParameterHealthConfig,
        DeviceProperty,
        DevicePerformanceTrend,
        IdleRunningLog,
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")

    stop_event = asyncio.Event()
    trends_task = None

    async def run_performance_trends_scheduler():
        from app.database import AsyncSessionLocal
        from app.services.performance_trends import PerformanceTrendService
        from app.services.idle_running import IdleRunningService

        interval_minutes = max(1, settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES)
        interval_seconds = interval_minutes * 60

        def seconds_until_next_boundary() -> float:
            now = datetime.now(timezone.utc)
            next_slot = (now + timedelta(minutes=interval_minutes)).replace(second=0, microsecond=0)
            rounded_minute = (next_slot.minute // interval_minutes) * interval_minutes
            next_slot = next_slot.replace(minute=rounded_minute)
            if next_slot <= now:
                next_slot = now + timedelta(seconds=interval_seconds)
            return (next_slot - now).total_seconds()

        while not stop_event.is_set():
            try:
                async with AsyncSessionLocal() as session:
                    service = PerformanceTrendService(session)
                    summary = await service.materialize_latest_bucket()
                    idle_service = IdleRunningService(session)
                    idle_summary = await idle_service.aggregate_all_configured_devices()
                    safe_summary = {
                        "devices_total": summary.get("devices_total", 0),
                        "created_count": summary.get("created", 0),
                        "updated_count": summary.get("updated", 0),
                        "failed_count": summary.get("failed", 0),
                        "idle_processed": idle_summary.get("processed", 0),
                        "idle_failed": idle_summary.get("failed", 0),
                    }
                    logger.info(
                        "Performance trends and idle aggregation cycle completed",
                        extra=safe_summary,
                    )
            except Exception as exc:
                logger.error(
                    "Performance trends scheduler failed",
                    extra={"error": str(exc)},
                )

            wait_seconds = seconds_until_next_boundary()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass

    if settings.PERFORMANCE_TRENDS_ENABLED and settings.PERFORMANCE_TRENDS_CRON_ENABLED:
        trends_task = asyncio.create_task(run_performance_trends_scheduler())
        logger.info(
            "Performance trends scheduler started",
            extra={
                "interval_minutes": settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES,
                "timezone": settings.PERFORMANCE_TRENDS_TIMEZONE,
            },
        )
    
    yield
    
    # Shutdown
    if trends_task:
        stop_event.set()
        try:
            await asyncio.wait_for(trends_task, timeout=10)
        except asyncio.TimeoutError:
            trends_task.cancel()

    logger.info("Shutting down Device Service - closing database connections")
    await engine.dispose()
    logger.info("Device Service shutdown complete")


app = FastAPI(
    title="Device Service",
    description="Energy Intelligence Platform - Device Management Service",
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

app.include_router(api_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Invalid request payload",
            "code": "VALIDATION_ERROR",
            "details": exc.errors(),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        payload = dict(exc.detail)
        payload.setdefault("code", payload.get("error", "HTTP_ERROR"))
        payload.setdefault("message", "Request failed")
        return JSONResponse(status_code=exc.status_code, content=payload)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTP_ERROR", "message": str(exc.detail), "code": "HTTP_ERROR"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception in device-service")
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "Unexpected server error",
            "code": "INTERNAL_ERROR",
        },
    )


@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint for Kubernetes probes."""
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "device-service",
            "version": settings.APP_VERSION,
        },
        status_code=200
    )


@app.get("/ready", tags=["health"])
async def readiness_check():
    """Readiness check endpoint for Kubernetes probes."""
    try:
        # Check database connectivity
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        
        return JSONResponse(
            content={
                "status": "ready",
                "service": "device-service",
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            content={
                "status": "not_ready",
                "service": "device-service",
                "error": str(e),
            },
            status_code=503
        )
