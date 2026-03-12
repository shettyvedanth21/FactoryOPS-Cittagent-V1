from fastapi import APIRouter

from src.ai.copilot_engine import CopilotEngine
from src.ai.model_client import AIUnavailableError, ModelClient
from src.integrations.service_clients import get_current_tariff
from src.response.schema import ChatRequest, CopilotResponse


router = APIRouter()
model_client: ModelClient | None = None
engine: CopilotEngine | None = None


def _get_engine() -> tuple[ModelClient | None, CopilotEngine | None]:
    global model_client, engine
    if model_client and engine:
        return model_client, engine
    try:
        model_client = ModelClient()
        engine = CopilotEngine(model_client=model_client)
        return model_client, engine
    except Exception:
        return None, None


@router.post("/api/v1/copilot/chat", response_model=CopilotResponse)
async def chat(request: ChatRequest) -> CopilotResponse:
    model, copilot_engine = _get_engine()
    if not model or not copilot_engine:
        return CopilotResponse(
            answer="Copilot is not configured. Please add AI_PROVIDER and provider API key.",
            reasoning="Provider setup failed during initialization.",
            error_code="NOT_CONFIGURED",
        )

    if not model.is_provider_configured():
        return CopilotResponse(
            answer="Copilot is not configured. Please add AI_PROVIDER and provider API key.",
            reasoning="Provider config missing in environment.",
            error_code="NOT_CONFIGURED",
        )

    tariff_rate, currency = await get_current_tariff()

    history_payload = [t.model_dump() for t in request.conversation_history]

    try:
        return await copilot_engine.process_question(
            message=request.message,
            history=history_payload,
            tariff_rate=tariff_rate,
            currency=currency,
        )
    except AIUnavailableError:
        return CopilotResponse(
            answer="AI service is temporarily unavailable. Please try again.",
            reasoning="Provider request failed.",
            error_code="AI_UNAVAILABLE",
        )
    except Exception:
        return CopilotResponse(
            answer="Something went wrong. Please try again.",
            reasoning="Unexpected server error while processing copilot request.",
            error_code="INTERNAL_ERROR",
        )
