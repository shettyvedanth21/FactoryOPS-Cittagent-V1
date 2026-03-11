"""Analytics Service entry point."""

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.routes import analytics, health
from src.config.logging_config import configure_logging
from src.config.settings import Settings, get_settings
from src.workers.job_queue import JobQueue
from src.workers.job_worker import JobWorker

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    settings = get_settings()
    
    configure_logging(settings.log_level)
    logger.info("analytics_service_starting", version="1.0.0")
    
    job_queue = JobQueue()
    job_worker = JobWorker(job_queue)
    
    worker_task = asyncio.create_task(job_worker.start())
    app.state.job_queue = job_queue
    app.state.job_worker = job_worker
    app.state.fleet_tasks = set()

    _retrainer = None
    _retrainer_task = None
    if settings.ml_weekly_retrainer_enabled:
        from src.infrastructure.s3_client import S3Client
        from src.services.analytics.retrainer import WeeklyRetrainer
        from src.services.dataset_service import DatasetService

        _retrainer = WeeklyRetrainer(
            job_queue=job_queue,
            dataset_service=DatasetService(S3Client()),
        )
        _retrainer_task = asyncio.create_task(_retrainer.start(device_ids=[]))
        app.state.retrainer = _retrainer
    
    logger.info("analytics_service_ready")
    
    yield
    
    logger.info("analytics_service_shutting_down")
    await job_worker.stop()
    for task in list(app.state.fleet_tasks):
        task.cancel()
    if app.state.fleet_tasks:
        await asyncio.gather(*app.state.fleet_tasks, return_exceptions=True)
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    if settings.ml_weekly_retrainer_enabled and hasattr(app.state, "retrainer") and _retrainer:
        await _retrainer.stop()
        if _retrainer_task:
            _retrainer_task.cancel()
            try:
                await _retrainer_task
            except asyncio.CancelledError:
                pass
    logger.info("analytics_service_stopped")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()
    
    app = FastAPI(
        title="Analytics Service",
        description="ML Analytics Service for Energy Intelligence Platform",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    
    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"])

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
        logger.exception("Unhandled exception in analytics-service")
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_ERROR",
                "message": "Unexpected server error",
                "code": "INTERNAL_ERROR",
            },
        )
    
    return app


app = create_app()
