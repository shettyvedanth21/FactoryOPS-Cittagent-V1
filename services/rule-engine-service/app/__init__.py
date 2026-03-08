"""Rule Engine Service - Energy Intelligence Platform.

This module initializes the FastAPI application with all required configurations.
"""

import asyncio
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
        "Starting Rule Engine Service",
        extra={
            "service": "rule-engine-service",
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
        }
    )
    
    yield
    
    # Shutdown
    logger.info("Shutting down Rule Engine Service - closing database connections")
    await engine.dispose()
    logger.info("Rule Engine Service shutdown complete")


app = FastAPI(
    title="Rule Engine Service",
    description="Energy Intelligence Platform - Real-time Rule Evaluation Service",
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
    logger.exception("Unhandled exception in rule-engine-service")
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
            "service": "rule-engine-service",
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
                "service": "rule-engine-service",
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            content={
                "status": "not_ready",
                "service": "rule-engine-service",
                "error": str(e),
            },
            status_code=503
        )
