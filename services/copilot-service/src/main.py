import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.ai.model_client import ModelClient
from src.api.chat import router as chat_router
from src.config import settings
from src.database import engine
from src.db.schema_loader import load_schema


logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

startup_state = {
    "schema_loaded": False,
    "provider_configured": False,
    "provider_ping": False,
    "db_ready": False,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        startup_state["db_ready"] = True

        await load_schema()
        startup_state["schema_loaded"] = True

        model_client = ModelClient()
        startup_state["provider_configured"] = model_client.is_provider_configured()
        startup_state["provider_ping"] = await model_client.ping() if startup_state["provider_configured"] else False

        logger.info("copilot_startup_complete", extra=startup_state)
    except Exception as exc:
        logger.exception("copilot_startup_failed", extra={"error": str(exc)})
    yield
    await engine.dispose()


app = FastAPI(title="Factory Copilot Service", version="1.0.0", lifespan=lifespan)
app.include_router(chat_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "answer": "Invalid request payload.",
            "details": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception in copilot-service")
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "answer": "Something went wrong.",
        },
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": settings.ai_provider,
        "provider_configured": startup_state["provider_configured"],
    }


@app.get("/ready")
async def ready():
    ready_flag = all([startup_state["db_ready"], startup_state["schema_loaded"]])
    return {
        "status": "ready" if ready_flag else "not_ready",
        "checks": startup_state,
    }
